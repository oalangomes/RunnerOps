#!/usr/bin/env python3
"""Governed autoscale controller for exact local START_LOCAL activation.

Slice #71 intentionally applies only pre-provisioned local capacity. It never
registers a runner and never contacts a cloud provider.
"""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import capacity
from autoscale_contracts import AuditError, action_record, timestamp, utcnow
from autoscale_planner import PolicyError, collect_host_facts, load_policy, plan, policy_fingerprint
from autoscale_runtime import decision_from_plan, pending_start_actions, read_planner_evidence
from autoscale_store import AuditStore, database_path

SCHEMA_VERSION = 1
REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"


class ControllerError(Exception):
    def __init__(self, code, exit_code=3):
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


def _bool_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ControllerError("INVALID_AUTOSCALE_ENABLED", 2)


def autoscale_enabled():
    return _bool_env("RUNNER_AUTOSCALE_ENABLED", False)


def _integer_env(name, default, minimum, maximum):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ControllerError("INVALID_CONTROLLER_POLICY", 2) from None
    if not minimum <= value <= maximum:
        raise ControllerError("INVALID_CONTROLLER_POLICY", 2)
    return value


@contextmanager
def controller_lock(path=None):
    """One non-blocking autoscale mutator per host/state root."""
    lock_path = Path(path) if path is not None else database_path().parent / "autoscale-controller.lock"
    root = lock_path.parent
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.stat().st_mode & 0o077:
            raise ControllerError("STATE_PERMISSIONS_UNSAFE")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.fstat(descriptor).st_mode & 0o077:
            os.close(descriptor)
            raise ControllerError("LOCK_PERMISSIONS_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ControllerError("CONTROLLER_BUSY") from None
            raise
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    except ControllerError:
        raise
    except OSError:
        raise ControllerError("CONTROLLER_LOCK_UNAVAILABLE") from None


def _action_id(decision_id, target):
    basis = decision_id + "\0START_LOCAL\0" + target
    return "action-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _registration_id(runner):
    registration_id = runner.get("registration_id")
    github = runner.get("github") or {}
    if type(registration_id) is not int or registration_id <= 0 or github.get("id") != registration_id:
        return None
    return registration_id


def _target_record(snapshot, target, expected_registration_id=None):
    if (
        not isinstance(target, str)
        or not target
        or target == "all"
        or target.startswith("group:")
        or snapshot.get("sources", {}).get("local") != "complete"
        or snapshot.get("sources", {}).get("github_runners") != "complete"
    ):
        return None
    matches = [
        runner for runner in snapshot.get("capacity", {}).get("runners", [])
        if runner.get("scope") == "local" and runner.get("name") == target
    ]
    if len(matches) != 1:
        return None
    runner = matches[0]
    registration_id = _registration_id(runner)
    if (
        runner.get("enabled") is not True
        or registration_id is None
        or (expected_registration_id is not None and str(registration_id) != str(expected_registration_id))
    ):
        return None
    return runner


def _idle_target_identity(snapshot, target, expected_registration_id=None):
    runner = _target_record(snapshot, target, expected_registration_id)
    if runner is None:
        return None
    local = runner.get("local") or {}
    github = runner.get("github") or {}
    if (
        runner.get("category") != "provisioned_idle"
        or local.get("state") != "healthy_idle"
        or github.get("status") != "offline"
        or github.get("busy") is not False
    ):
        return None
    return str(_registration_id(runner))


def _target_state(snapshot, target, expected_registration_id=None):
    runner = _target_record(snapshot, target, expected_registration_id)
    if runner is None:
        return "inconclusive"
    local = runner.get("local") or {}
    github = runner.get("github") or {}
    if (
        local.get("state") == "active"
        and github.get("status") == "online"
        and runner.get("category") in ("available_now", "busy_capacity")
    ):
        return "online"
    if (
        local.get("state") == "healthy_idle"
        and github.get("status") == "offline"
        and github.get("busy") is False
        and runner.get("category") == "provisioned_idle"
    ):
        return "idle"
    if (
        runner.get("category") == "inconclusive"
        or local.get("state") in (None, "unknown", "failed")
        or github.get("status") not in ("online", "offline")
    ):
        return "inconclusive"
    return "other"


def _action(plan_result, state, at, *, previous=None, diagnostic=None, exit_code=None, external_id=None):
    target = plan_result["action"]["target"]
    started_at = previous["started_at"] if previous else None
    finished_at = None
    if state == "started" and started_at is None:
        started_at = at
    elif state in ("succeeded", "failed", "cancelled"):
        if started_at is None:
            raise ControllerError("ACTION_START_TIME_MISSING")
        finished_at = at
    if previous is not None:
        external_id = previous["external_id"]
    return action_record({
        "action_id": _action_id(plan_result["decision_id"], target),
        "decision_id": plan_result["decision_id"],
        "kind": "START_LOCAL",
        "target": target,
        "state": state,
        "timestamp": at,
        "started_at": started_at,
        "finished_at": finished_at,
        "external_id": external_id,
        "diagnostic": {"code": diagnostic, "exit_code": exit_code},
    })


def _transition_existing(action, state, at, diagnostic, exit_code=None):
    started_at = action["started_at"]
    if state == "started" and started_at is None:
        started_at = at
    finished_at = at if state in ("succeeded", "failed", "cancelled") else None
    return action_record({
        **action,
        "state": state,
        "timestamp": at,
        "started_at": started_at,
        "finished_at": finished_at,
        "diagnostic": {"code": diagnostic, "exit_code": exit_code},
    })


def start_exact_runner(target):
    """Invoke the existing exact lifecycle boundary; never group/all."""
    if not isinstance(target, str) or not target or target == "all" or target.startswith("group:"):
        raise ControllerError("INVALID_START_TARGET", 2)
    try:
        result = subprocess.run(
            [str(Path(__file__).resolve().parent / "runners.sh"), "start", target],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return result.returncode


def verify_exact_runner(
    repository,
    target,
    expected_registration_id,
    *,
    snapshot_fn=capacity.snapshot,
    observe_fn=None,
):
    """Verify exact local/systemd/registration/GitHub state from CapacitySnapshot."""
    timeout_seconds = _integer_env("RUNNER_AUTOSCALE_VERIFY_TIMEOUT_SECONDS", 20, 1, 300)
    interval_seconds = _integer_env("RUNNER_AUTOSCALE_VERIFY_INTERVAL_SECONDS", 1, 1, 30)
    deadline = time.monotonic() + timeout_seconds
    saw_inconclusive = False
    while True:
        observed = snapshot_fn(repository)
        if observe_fn is not None:
            observe_fn(observed)
        state = _target_state(observed, target, expected_registration_id)
        if state == "online":
            return True, "VERIFIED_ONLINE"
        saw_inconclusive = saw_inconclusive or state == "inconclusive"
        if time.monotonic() >= deadline:
            return False, "EVIDENCE_INCONCLUSIVE" if saw_inconclusive else "GITHUB_VERIFICATION_FAILED"
        time.sleep(interval_seconds)


def _controller_result(plan_result=None, *, decision=None, status, action=None, diagnostic=None):
    source = decision or plan_result or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "AutoscaleControllerResult",
        "status": status,
        "repository": source.get("repository"),
        "decision_id": source.get("decision_id"),
        "decision": source.get("decision"),
        "reason_codes": source.get("reason_codes", []),
        "action_id": action.get("action_id") if action else None,
        "action_state": action.get("state") if action else None,
        "target": action.get("target") if action else None,
        "diagnostic": diagnostic,
    }


def _empty_audit(error):
    return {
        "status": "inconclusive",
        "error": error,
        "queue": [],
        "active_burst_capacity": None,
        "last_scaling_action_started_at": None,
    }


def _cancel_pending(store, decision, action, code, clock):
    terminal = _transition_existing(action, "cancelled", timestamp(clock().isoformat()), code, None)
    store.record_action(terminal)
    return _controller_result(
        decision=decision, status="noop", action=terminal, diagnostic=code
    ), 0


def _finish_start_attempt(store, decision, action, repository, *, start_fn, verify_fn, clock):
    start_rc = start_fn(action["target"])
    verified, verify_code = verify_fn(repository, action["target"], action["external_id"])
    finished_at = timestamp(clock().isoformat())
    if verified:
        diagnostic = verify_code if start_rc == 0 else "VERIFIED_ONLINE_AFTER_START_ERROR"
        terminal = _transition_existing(action, "succeeded", finished_at, diagnostic, start_rc)
        store.record_action(terminal)
        return _controller_result(
            decision=decision, status="ok", action=terminal, diagnostic=diagnostic
        ), 0

    diagnostic = "START_COMMAND_FAILED" if start_rc != 0 else verify_code
    terminal = _transition_existing(
        action, "failed", finished_at, diagnostic, start_rc if start_rc != 0 else 1
    )
    store.record_action(terminal)
    return _controller_result(
        decision=decision, status="failed", action=terminal, diagnostic=diagnostic
    ), 1


def _recover_pending(
    store,
    pending,
    fresh_snapshot,
    fresh_plan,
    fresh_policy,
    repository,
    *,
    start_fn,
    verify_fn,
    clock,
):
    decision = pending["decision"]
    action = pending["action"]
    expected_registration_id = action["external_id"]
    state = _target_state(fresh_snapshot, action["target"], expected_registration_id)
    at = timestamp(clock().isoformat())

    if state == "online":
        if action["state"] == "planned":
            return _cancel_pending(store, decision, action, "TARGET_ALREADY_ONLINE", clock)
        terminal = _transition_existing(action, "succeeded", at, "VERIFIED_ONLINE", 0)
        store.record_action(terminal)
        return _controller_result(
            decision=decision,
            status="ok",
            action=terminal,
            diagnostic="RECOVERED_VERIFIED_ACTION",
        ), 0

    if action["state"] == "started":
        if state == "inconclusive":
            return _controller_result(
                decision=decision,
                status="inconclusive",
                action=action,
                diagnostic="EVIDENCE_INCONCLUSIVE",
            ), 3
        # Reconciliation is not a new scaling decision. Do not issue a second
        # lifecycle start after the process may already have crossed that boundary.
        verified, code = verify_fn(repository, action["target"], expected_registration_id)
        terminal = _transition_existing(
            action,
            "succeeded" if verified else "failed",
            timestamp(clock().isoformat()),
            code,
            0 if verified else 1,
        )
        store.record_action(terminal)
        return _controller_result(
            decision=decision,
            status="ok" if verified else "failed",
            action=terminal,
            diagnostic=code,
        ), 0 if verified else 1

    same_policy = policy_fingerprint(fresh_policy) == decision["policy_fingerprint"]
    if not same_policy:
        return _cancel_pending(store, decision, action, "POLICY_CHANGED", clock)
    if fresh_plan["decision"] == "INCONCLUSIVE":
        return _controller_result(
            decision=decision,
            status="inconclusive",
            action=action,
            diagnostic="EVIDENCE_INCONCLUSIVE",
        ), 3
    same_plan = (
        fresh_plan["decision"] == "START_LOCAL"
        and fresh_plan.get("action", {}).get("target") == action["target"]
    )
    registration_id = _idle_target_identity(
        fresh_snapshot, action["target"], expected_registration_id
    )
    if not same_plan:
        return _cancel_pending(store, decision, action, "PLAN_CHANGED", clock)
    if registration_id is None:
        return _cancel_pending(store, decision, action, "TARGET_CHANGED", clock)

    if action["external_id"] is None:
        started = action_record({
            **action,
            "state": "started",
            "timestamp": at,
            "started_at": at,
            "finished_at": None,
            "external_id": registration_id,
            "diagnostic": {"code": "START_REQUESTED", "exit_code": None},
        })
    else:
        started = _transition_existing(action, "started", at, "START_REQUESTED", None)
    store.record_action(started)
    return _finish_start_attempt(
        store,
        decision,
        started,
        repository,
        start_fn=start_fn,
        verify_fn=verify_fn,
        clock=clock,
    )


def run_once(
    repository=".",
    *,
    enabled=None,
    snapshot_fn=capacity.snapshot,
    policy_loader=load_policy,
    host_collector=collect_host_facts,
    store_factory=None,
    lock_factory=controller_lock,
    start_fn=start_exact_runner,
    verify_fn=None,
    clock=utcnow,
):
    """Run one governed controller iteration and apply at most one exact START_LOCAL."""
    if enabled is None:
        enabled = autoscale_enabled()
    if not enabled:
        return _controller_result(status="disabled", diagnostic="AUTOSCALE_DISABLED"), 0

    initial_policy = policy_loader()
    store_factory = store_factory or (lambda: AuditStore(writable=True))
    initial_snapshot = snapshot_fn(repository)
    canonical = initial_snapshot.get("repository", {}).get("nameWithOwner")
    if not canonical:
        result = plan(
            initial_snapshot,
            initial_policy,
            host_collector(initial_policy),
            _empty_audit("canonical_identity_unavailable"),
        )
        return _controller_result(
            result, status="inconclusive", diagnostic="EVIDENCE_INCONCLUSIVE"
        ), 3

    initial_decision = None
    initial_registration_id = None
    initial_pending = []

    # Observe and decide before entering the mutating critical section.
    with store_factory() as store:
        store.observe(initial_snapshot)
        audit = read_planner_evidence(store, canonical)
        initial_plan = plan(initial_snapshot, initial_policy, host_collector(initial_policy), audit)
        initial_pending = pending_start_actions(store, canonical)

        if len(initial_pending) > 1:
            return _controller_result(
                initial_plan,
                status="inconclusive",
                diagnostic="MULTIPLE_PENDING_START_ACTIONS",
            ), 3

        if not initial_pending:
            if initial_plan["decision"] == "INCONCLUSIVE":
                return _controller_result(
                    initial_plan, status="inconclusive", diagnostic="EVIDENCE_INCONCLUSIVE"
                ), 3
            if initial_plan["decision"] != "START_LOCAL":
                return _controller_result(
                    initial_plan, status="noop", diagnostic="DECISION_NOT_APPLIED_IN_SLICE"
                ), 0

            target = initial_plan.get("action", {}).get("target")
            initial_registration_id = _idle_target_identity(initial_snapshot, target)
            if initial_registration_id is None:
                return _controller_result(
                    initial_plan, status="inconclusive", diagnostic="START_TARGET_INCONCLUSIVE"
                ), 3

            initial_decision = decision_from_plan(initial_plan)
            # The decision is durable before lock/action/lifecycle mutation.
            store.record_decision(initial_decision)

    lock_decision = initial_pending[0]["decision"] if initial_pending else initial_decision
    try:
        with lock_factory():
            with store_factory() as store:
                fresh_policy = policy_loader()
                fresh_snapshot = snapshot_fn(repository)
                fresh_canonical = fresh_snapshot.get("repository", {}).get("nameWithOwner")
                if not fresh_canonical or fresh_canonical.casefold() != canonical.casefold():
                    return _controller_result(
                        decision=lock_decision,
                        status="inconclusive",
                        diagnostic="REPOSITORY_CHANGED",
                    ), 3

                store.observe(fresh_snapshot)
                fresh_audit = read_planner_evidence(store, canonical)
                fresh_plan = plan(
                    fresh_snapshot, fresh_policy, host_collector(fresh_policy), fresh_audit
                )
                pending = pending_start_actions(store, canonical)
                if len(pending) > 1:
                    return _controller_result(
                        decision=lock_decision,
                        status="inconclusive",
                        diagnostic="MULTIPLE_PENDING_START_ACTIONS",
                    ), 3

                active_verify = verify_fn or (
                    lambda repo, target, registration_id: verify_exact_runner(
                        repo,
                        target,
                        registration_id,
                        snapshot_fn=snapshot_fn,
                        observe_fn=store.observe,
                    )
                )

                if pending:
                    return _recover_pending(
                        store,
                        pending[0],
                        fresh_snapshot,
                        fresh_plan,
                        fresh_policy,
                        canonical,
                        start_fn=start_fn,
                        verify_fn=active_verify,
                        clock=clock,
                    )

                if initial_decision is None:
                    return _controller_result(
                        fresh_plan,
                        status="inconclusive",
                        diagnostic="RECOVERY_STATE_CHANGED",
                    ), 3

                same_policy = (
                    policy_fingerprint(fresh_policy)
                    == initial_decision["policy_fingerprint"]
                )
                if fresh_plan["decision"] == "INCONCLUSIVE":
                    return _controller_result(
                        decision=initial_decision,
                        status="inconclusive",
                        diagnostic="EVIDENCE_INCONCLUSIVE",
                    ), 3
                same_plan = (
                    fresh_plan["decision"] == "START_LOCAL"
                    and fresh_plan.get("action", {}).get("target")
                    == initial_plan["action"]["target"]
                )
                fresh_registration_id = _idle_target_identity(
                    fresh_snapshot,
                    initial_plan["action"]["target"],
                    initial_registration_id,
                )
                if not same_policy:
                    return _controller_result(
                        decision=initial_decision, status="noop", diagnostic="POLICY_CHANGED"
                    ), 0
                if not same_plan:
                    return _controller_result(
                        decision=initial_decision, status="noop", diagnostic="PLAN_CHANGED"
                    ), 0
                if fresh_registration_id is None:
                    return _controller_result(
                        decision=initial_decision, status="noop", diagnostic="TARGET_CHANGED"
                    ), 0

                planned = _action(
                    initial_plan,
                    "planned",
                    timestamp(clock().isoformat()),
                    diagnostic="PLANNED",
                    exit_code=None,
                    external_id=initial_registration_id,
                )
                store.record_action(planned)
                started = _transition_existing(
                    planned,
                    "started",
                    timestamp(clock().isoformat()),
                    "START_REQUESTED",
                    None,
                )
                store.record_action(started)
                return _finish_start_attempt(
                    store,
                    initial_decision,
                    started,
                    canonical,
                    start_fn=start_fn,
                    verify_fn=active_verify,
                    clock=clock,
                )
    except ControllerError as exc:
        if exc.code != "CONTROLLER_BUSY":
            raise
        return _controller_result(
            decision=lock_decision, status="inconclusive", diagnostic="CONTROLLER_BUSY"
        ), 3


def render(result):
    print(f"Autoscale controller: {result['status']}")
    if result["repository"]:
        print(f"Repository: {result['repository']}")
    if result["decision"]:
        print(f"Decision: {result['decision']} id={result['decision_id']}")
    if result["action_id"]:
        print(f"Action: {result['action_id']} state={result['action_state']} target={result['target']}")
    if result["diagnostic"]:
        print(f"Diagnostic: {result['diagnostic']}")


def main():
    parser = argparse.ArgumentParser(
        prog="runnerctl autoscale run-once",
        description="Apply at most one governed START_LOCAL autoscale decision.",
    )
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.repository != "." and not re.fullmatch(REPO_PATTERN, args.repository):
        parser.error("expected owner/repo or .")

    try:
        result, exit_code = run_once(args.repository)
    except PolicyError:
        result = _controller_result(status="error", diagnostic="INVALID_POLICY")
        exit_code = 2
    except AuditError as exc:
        result = _controller_result(status="inconclusive", diagnostic=exc.code.upper())
        exit_code = 3
    except ControllerError as exc:
        result = _controller_result(
            status="error" if exc.exit_code == 2 else "inconclusive",
            diagnostic=exc.code,
        )
        exit_code = exc.exit_code

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        render(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
