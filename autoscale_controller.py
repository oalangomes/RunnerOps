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
from autoscale_planner import (
    PolicyError,
    collect_host_facts,
    load_policy,
    plan,
    policy_fingerprint,
)
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
    """Mutation is opt-in independently from the read-only planner."""

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
    digest = hashlib.sha256(
        (decision_id + "\0START_LOCAL\0" + target).encode("utf-8")
    ).hexdigest()
    return "action-" + digest[:32]


def _action(plan_result, state, at, *, previous=None, diagnostic=None, exit_code=None):
    target = plan_result["action"]["target"]
    started_at = previous["started_at"] if previous else None
    finished_at = None
    if state == "started" and started_at is None:
        started_at = at
    elif state in ("succeeded", "failed", "cancelled"):
        if started_at is None:
            raise ControllerError("ACTION_START_TIME_MISSING")
        finished_at = at
    return action_record(
        {
            "action_id": _action_id(plan_result["decision_id"], target),
            "decision_id": plan_result["decision_id"],
            "kind": "START_LOCAL",
            "target": target,
            "state": state,
            "timestamp": at,
            "started_at": started_at,
            "finished_at": finished_at,
            "external_id": None,
            "diagnostic": {"code": diagnostic, "exit_code": exit_code},
        }
    )


def _transition_existing(action, state, at, diagnostic, exit_code=None):
    started_at = action["started_at"]
    if state == "started" and started_at is None:
        started_at = at
    finished_at = at if state in ("succeeded", "failed", "cancelled") else None
    return action_record(
        {
            **action,
            "state": state,
            "timestamp": at,
            "started_at": started_at,
            "finished_at": finished_at,
            "diagnostic": {"code": diagnostic, "exit_code": exit_code},
        }
    )


def _target_state(snapshot, target):
    if (
        snapshot.get("sources", {}).get("local") != "complete"
        or snapshot.get("sources", {}).get("github_runners") != "complete"
    ):
        return "inconclusive"
    matches = [
        runner
        for runner in snapshot.get("capacity", {}).get("runners", [])
        if runner.get("scope") == "local" and runner.get("name") == target
    ]
    if len(matches) != 1:
        return "inconclusive"
    runner = matches[0]
    local = runner.get("local") or {}
    github = runner.get("github") or {}
    if (
        local.get("state") == "active"
        and github.get("status") == "online"
        and runner.get("category") in ("available_now", "busy_capacity")
    ):
        return "online"
    if runner.get("category") == "provisioned_idle" and local.get("state") == "healthy_idle":
        return "idle"
    if runner.get("category") == "inconclusive" or local.get("state") in (None, "unknown", "failed"):
        return "inconclusive"
    return "other"


def start_exact_runner(target):
    """Invoke the existing exact lifecycle boundary; never group/all."""

    if not isinstance(target, str) or not target or target == "all" or target.startswith("group:"):
        raise ControllerError("INVALID_START_TARGET", 2)
    command = [str(Path(__file__).resolve().parent / "runners.sh"), "start", target]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return result.returncode


def verify_exact_runner(repository, target, *, snapshot_fn=capacity.snapshot):
    """Require local status+health and GitHub online evidence after activation."""

    runner_script = str(Path(__file__).resolve().parent / "runners.sh")
    for command in ("status", "health"):
        try:
            result = subprocess.run(
                [runner_script, command, target],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, "LOCAL_VERIFICATION_FAILED"
        if result.returncode != 0:
            return False, "LOCAL_VERIFICATION_FAILED"

    timeout_seconds = _integer_env(
        "RUNNER_AUTOSCALE_VERIFY_TIMEOUT_SECONDS", 20, 1, 300
    )
    interval_seconds = _integer_env(
        "RUNNER_AUTOSCALE_VERIFY_INTERVAL_SECONDS", 1, 1, 30
    )
    deadline = time.monotonic() + timeout_seconds
    saw_inconclusive = False
    while True:
        observed = snapshot_fn(repository)
        state = _target_state(observed, target)
        if state == "online":
            return True, "VERIFIED_ONLINE"
        saw_inconclusive = saw_inconclusive or state == "inconclusive"
        if time.monotonic() >= deadline:
            return False, "EVIDENCE_INCONCLUSIVE" if saw_inconclusive else "GITHUB_VERIFICATION_FAILED"
        time.sleep(interval_seconds)


def _controller_result(plan_result=None, *, status, action=None, diagnostic=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "AutoscaleControllerResult",
        "status": status,
        "repository": plan_result.get("repository") if plan_result else None,
        "decision_id": plan_result.get("decision_id") if plan_result else None,
        "decision": plan_result.get("decision") if plan_result else None,
        "reason_codes": plan_result.get("reason_codes", []) if plan_result else [],
        "action_id": action.get("action_id") if action else None,
        "action_state": action.get("state") if action else None,
        "target": action.get("target") if action else None,
        "diagnostic": diagnostic,
    }


def _cancel_or_fail_pending(store, action, at, code):
    if action["state"] == "planned":
        cancelled = _transition_existing(action, "cancelled", at, code, None)
        store.record_action(cancelled)
        return cancelled
    failed = _transition_existing(action, "failed", at, code, 1)
    store.record_action(failed)
    return failed


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

    policy = policy_loader()
    store_factory = store_factory or (lambda: AuditStore(writable=True))
    verify_fn = verify_fn or (
        lambda repo, target: verify_exact_runner(repo, target, snapshot_fn=snapshot_fn)
    )

    with lock_factory():
        current_snapshot = snapshot_fn(repository)
        canonical = current_snapshot.get("repository", {}).get("nameWithOwner")
        if not canonical:
            result = plan(
                current_snapshot,
                policy,
                host_collector(policy),
                {"status": "inconclusive", "error": "canonical_identity_unavailable", "queue": [], "active_burst_capacity": None, "last_scaling_action_started_at": None},
            )
            return _controller_result(result, status="inconclusive", diagnostic="EVIDENCE_INCONCLUSIVE"), 3

        with store_factory() as store:
            store.observe(current_snapshot)
            audit = read_planner_evidence(store, canonical)
            plan_result = plan(current_snapshot, policy, host_collector(policy), audit)
            pending = pending_start_actions(store, canonical)
            if len(pending) > 1:
                return _controller_result(
                    plan_result,
                    status="inconclusive",
                    diagnostic="MULTIPLE_PENDING_START_ACTIONS",
                ), 3

            if pending:
                previous_decision = pending[0]["decision"]
                action = pending[0]["action"]
                target_state = _target_state(current_snapshot, action["target"])
                at = timestamp(clock().isoformat())

                if target_state == "online":
                    if action["state"] == "planned":
                        action = _transition_existing(
                            action, "started", at, "RECOVERED_ALREADY_ONLINE", 0
                        )
                        store.record_action(action)
                    succeeded = _transition_existing(
                        action, "succeeded", at, "VERIFIED_ONLINE", 0
                    )
                    store.record_action(succeeded)
                    return _controller_result(
                        plan_result,
                        status="ok",
                        action=succeeded,
                        diagnostic="RECOVERED_VERIFIED_ACTION",
                    ), 0

                # A previously started action is reconciliation work, not a new
                # scaling decision. Never issue a second start after process restart.
                if action["state"] == "started":
                    if target_state == "inconclusive":
                        return _controller_result(
                            plan_result,
                            status="inconclusive",
                            action=action,
                            diagnostic="EVIDENCE_INCONCLUSIVE",
                        ), 3
                    verified, code = verify_fn(canonical, action["target"])
                    terminal = _transition_existing(
                        action,
                        "succeeded" if verified else "failed",
                        timestamp(clock().isoformat()),
                        code,
                        0 if verified else 1,
                    )
                    store.record_action(terminal)
                    return _controller_result(
                        plan_result,
                        status="ok" if verified else "failed",
                        action=terminal,
                        diagnostic=code,
                    ), 0 if verified else 1

                current_policy = policy_loader()
                same_policy = (
                    policy_fingerprint(current_policy)
                    == previous_decision["policy_fingerprint"]
                )
                same_plan = (
                    plan_result["decision"] == "START_LOCAL"
                    and plan_result.get("action", {}).get("target") == action["target"]
                )
                if not same_policy or not same_plan:
                    terminal = _cancel_or_fail_pending(
                        store,
                        action,
                        at,
                        "POLICY_CHANGED" if not same_policy else "PLAN_CHANGED",
                    )
                    return _controller_result(
                        plan_result,
                        status="inconclusive" if action["state"] == "started" else "noop",
                        action=terminal,
                        diagnostic="POLICY_CHANGED" if not same_policy else "PLAN_CHANGED",
                    ), 3 if action["state"] == "started" else 0

                if target_state == "inconclusive":
                    return _controller_result(
                        plan_result,
                        status="inconclusive",
                        action=action,
                        diagnostic="EVIDENCE_INCONCLUSIVE",
                    ), 3

                if action["state"] == "planned":
                    action = _transition_existing(
                        action, "started", at, "START_REQUESTED", None
                    )
                    store.record_action(action)
                start_rc = start_fn(action["target"])
                if start_rc != 0:
                    failed = _transition_existing(
                        action,
                        "failed",
                        timestamp(clock().isoformat()),
                        "START_COMMAND_FAILED",
                        start_rc,
                    )
                    store.record_action(failed)
                    return _controller_result(
                        plan_result,
                        status="failed",
                        action=failed,
                        diagnostic="START_COMMAND_FAILED",
                    ), 1

                verified, code = verify_fn(canonical, action["target"])
                finished_at = timestamp(clock().isoformat())
                terminal = _transition_existing(
                    action,
                    "succeeded" if verified else "failed",
                    finished_at,
                    code,
                    0 if verified else 1,
                )
                store.record_action(terminal)
                return _controller_result(
                    plan_result,
                    status="ok" if verified else "failed",
                    action=terminal,
                    diagnostic=code,
                ), 0 if verified else 1

            if plan_result["decision"] == "INCONCLUSIVE":
                return _controller_result(
                    plan_result,
                    status="inconclusive",
                    diagnostic="EVIDENCE_INCONCLUSIVE",
                ), 3

            if plan_result["decision"] != "START_LOCAL":
                return _controller_result(
                    plan_result,
                    status="noop",
                    diagnostic="DECISION_NOT_APPLIED_IN_SLICE",
                ), 0

            # A deterministic plan already terminal in the audit journal must
            # never replay its lifecycle mutation.
            try:
                previous = store.explain(plan_result["decision_id"])
            except AuditError as exc:
                if exc.code != "decision_not_found":
                    raise
            else:
                expected = _action_id(
                    plan_result["decision_id"], plan_result["action"]["target"]
                )
                matches = [item for item in previous["actions"] if item["action_id"] == expected]
                if len(matches) != 1 or matches[0]["state"] not in (
                    "succeeded", "failed", "cancelled"
                ):
                    return _controller_result(
                        plan_result, status="inconclusive", diagnostic="AUDIT_REPLAY_INCONCLUSIVE"
                    ), 3
                terminal = matches[0]
                return _controller_result(
                    plan_result,
                    status="ok" if terminal["state"] == "succeeded" else "noop",
                    action=terminal,
                    diagnostic="ACTION_ALREADY_TERMINAL",
                ), 0

            # TOCTOU guard: re-read policy after planning and before persisting or
            # mutating. The controller never executes a plan under a new policy.
            current_policy = policy_loader()
            if policy_fingerprint(current_policy) != plan_result["policy_fingerprint"]:
                return _controller_result(
                    plan_result,
                    status="inconclusive",
                    diagnostic="POLICY_CHANGED",
                ), 3

            decision = decision_from_plan(plan_result)
            at = timestamp(clock().isoformat())
            planned = _action(
                plan_result,
                "planned",
                at,
                diagnostic="PLANNED",
                exit_code=None,
            )
            store.record_decision(decision, [planned])
            started = _transition_existing(
                planned, "started", timestamp(clock().isoformat()), "START_REQUESTED", None
            )
            store.record_action(started)

            start_rc = start_fn(started["target"])
            if start_rc != 0:
                failed = _transition_existing(
                    started,
                    "failed",
                    timestamp(clock().isoformat()),
                    "START_COMMAND_FAILED",
                    start_rc,
                )
                store.record_action(failed)
                return _controller_result(
                    plan_result,
                    status="failed",
                    action=failed,
                    diagnostic="START_COMMAND_FAILED",
                ), 1

            verified, code = verify_fn(canonical, started["target"])
            terminal = _transition_existing(
                started,
                "succeeded" if verified else "failed",
                timestamp(clock().isoformat()),
                code,
                0 if verified else 1,
            )
            store.record_action(terminal)
            return _controller_result(
                plan_result,
                status="ok" if verified else "failed",
                action=terminal,
                diagnostic=code,
            ), 0 if verified else 1


def render(result):
    print(f"Autoscale controller: {result['status']}")
    if result["repository"]:
        print(f"Repository: {result['repository']}")
    if result["decision"]:
        print(f"Decision: {result['decision']} id={result['decision_id']}")
    if result["action_id"]:
        print(
            f"Action: {result['action_id']} state={result['action_state']} target={result['target']}"
        )
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
