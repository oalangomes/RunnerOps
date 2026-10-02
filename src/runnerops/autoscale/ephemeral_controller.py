"""Governed bridge from one durable autoscale action to the existing lifecycle."""

import hashlib
import json

from runnerops.ephemeral.contracts import LifecycleState
from runnerops.ephemeral.identity import runner_identity, validate_action_id
from runnerops.ephemeral.lifecycle import CleanupRefused

from .contracts import action_record, canonical_repo, decision_record, timestamp
from .planner import policy_fingerprint


def planned_action(plan, at):
    if plan.get("decision") != "CREATE_EPHEMERAL":
        raise ValueError("not_ephemeral_plan")
    target = plan.get("action", {}).get("target")
    if not isinstance(target, str):
        raise ValueError("invalid_ephemeral_target")
    validate_action_id(target)
    action_id = "action-" + hashlib.sha256(
        (plan["decision_id"] + "\0CREATE_EPHEMERAL\0" + target).encode("utf-8")
    ).hexdigest()[:32]
    return action_record({
        "action_id": action_id, "decision_id": plan["decision_id"],
        "kind": "CREATE_EPHEMERAL", "target": target, "state": "planned",
        "timestamp": timestamp(at), "started_at": None, "finished_at": None,
        "external_id": None, "diagnostic": {"code": "EPHEMERAL_PLANNED", "exit_code": None},
    })


def governed_actions(store, repository):
    repository = canonical_repo(repository)
    with store._read_transaction():
        rows = store.connection.execute(
            """SELECT d.payload AS decision_payload, a.payload AS action_payload
            FROM actions a JOIN decisions d USING(decision_id)
            WHERE lower(d.repository)=lower(?) AND a.state IN ('planned','started','succeeded')
              AND a.payload LIKE '%CREATE_EPHEMERAL%'
            ORDER BY a.updated_at, a.action_id LIMIT 1001""", (repository,)
        ).fetchall()
    if len(rows) > 1000:
        raise ValueError("too_many_ephemeral_actions")
    result = []
    for row in rows:
        decision = decision_record(json.loads(row["decision_payload"]))
        action = action_record(json.loads(row["action_payload"]))
        if action["kind"] == "CREATE_EPHEMERAL":
            result.append({"decision": decision, "action": action})
    return result


def _transition(action, state, at, code, external_id=None):
    at = timestamp(at)
    return action_record({**action, "state": state, "timestamp": at,
                          "started_at": action["started_at"] or at,
                          "finished_at": at if state == "succeeded" else None,
                          "external_id": external_id or action["external_id"],
                          "diagnostic": {"code": code, "exit_code": None}})


def _result(decision, action, status, diagnostic, lifecycle=None):
    return {"status": status, "repository": decision["repository"],
            "decision_id": decision["decision_id"], "decision": "CREATE_EPHEMERAL",
            "reason_codes": decision["reason_codes"], "action_id": action["action_id"],
            "action_state": action["state"], "target": action["target"],
            "ephemeral_action_id": action["target"],
            "ephemeral_state": lifecycle.action_state if lifecycle else None,
            "diagnostic": diagnostic}


def _matches_exact_spec(existing, exact_id, decision):
    ephemeral = decision["evidence"].get("ephemeral", {})
    selected = ephemeral.get("selected_scope_labels", [])
    template = ephemeral.get("template_labels", [])
    profile = ephemeral.get("profile")
    return (
        profile is not None
        and bool(template)
        and bool(selected)
        and existing.action_id == exact_id
        and existing.runner_identity == runner_identity(exact_id)
        and existing.repository.casefold() == decision["repository"].casefold()
        and (profile is None or existing.profile == profile)
        and (not template or {label.casefold() for label in existing.labels}
             == {label.casefold() for label in template} | {runner_identity(exact_id).casefold()})
        and {label.casefold() for label in selected}
        <= {label.casefold() for label in existing.labels}
    )


def reconcile_or_apply(store, pending, fresh_plan, fresh_policy, lifecycle, *, clock):
    decision, action = pending["decision"], pending["action"]
    exact_id = action["target"]
    now = lambda: timestamp(clock().isoformat())
    exists = lifecycle.store.exists(exact_id)
    if exists:
        try:
            existing = lifecycle.store.load(exact_id)
            matches = _matches_exact_spec(existing, exact_id, decision)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
            return _result(decision, action, "inconclusive", "EPHEMERAL_ACTION_UNREADABLE"), 3
        if not matches:
            return _result(decision, action, "inconclusive", "EPHEMERAL_IDENTITY_MISMATCH"), 3

    if action["state"] == "planned" and not exists:
        if policy_fingerprint(fresh_policy) != decision["policy_fingerprint"] or (
            fresh_plan.get("decision") != "CREATE_EPHEMERAL"
            or fresh_plan.get("evidence", {}).get("ephemeral", {}).get("selected_scope_labels")
            != decision["evidence"].get("ephemeral", {}).get("selected_scope_labels")
        ):
            at = now()
            cancelled = action_record({**action, "state": "cancelled", "timestamp": at,
                                       "started_at": None, "finished_at": at,
                                       "diagnostic": {"code": "PLAN_CHANGED", "exit_code": None}})
            store.record_action(cancelled)
            return _result(decision, cancelled, "noop", "PLAN_CHANGED"), 0
        config = fresh_policy["local_ephemeral"]
        try:
            observed = lifecycle.create(decision["repository"], config["profile"], config["labels"],
                                        action_id=exact_id)
        except (OSError, RuntimeError, ValueError):
            if not lifecycle.store.exists(exact_id):
                return _result(decision, action, "inconclusive", "EPHEMERAL_CREATE_UNCERTAIN"), 3
            try:
                if not _matches_exact_spec(lifecycle.store.load(exact_id), exact_id, decision):
                    return _result(decision, action, "inconclusive", "EPHEMERAL_IDENTITY_MISMATCH"), 3
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                return _result(decision, action, "inconclusive", "EPHEMERAL_ACTION_UNREADABLE"), 3
            observed = None
        started = _transition(action, "started", now(), "EPHEMERAL_REQUESTED",
                              runner_identity(exact_id))
        store.record_action(started)
        action = started
        if observed is None:
            try:
                observed = lifecycle.reconcile(exact_id)
            except (OSError, RuntimeError, ValueError):
                return _result(decision, action, "inconclusive", "EPHEMERAL_RECONCILIATION_REQUIRED"), 3
    else:
        if not exists:
            return _result(decision, action, "inconclusive", "EPHEMERAL_ACTION_MISSING"), 3
        if action["state"] == "planned":
            action = _transition(action, "started", now(), "EPHEMERAL_REQUESTED",
                                 runner_identity(exact_id))
            store.record_action(action)
        try:
            observed = lifecycle.reconcile(exact_id)
        except (OSError, RuntimeError, ValueError):
            return _result(decision, action, "inconclusive", "EPHEMERAL_RECONCILIATION_REQUIRED"), 3

    if observed.repository.casefold() != decision["repository"].casefold():
        return _result(decision, action, "inconclusive", "EPHEMERAL_IDENTITY_MISMATCH", observed), 3
    if observed.action_state == LifecycleState.REQUESTED.value:
        registration = getattr(observed, "registration", {})
        retry_proven_safe = (registration.get("attempted") is False
                             or registration.get("safe_retry_authorized") is True)
        if retry_proven_safe and policy_fingerprint(fresh_policy) == decision["policy_fingerprint"]:
            ephemeral = decision["evidence"].get("ephemeral", {})
            try:
                observed = lifecycle.create(decision["repository"], ephemeral["profile"],
                                            ephemeral["template_labels"], action_id=exact_id)
            except (OSError, RuntimeError, ValueError, KeyError):
                return _result(decision, action, "inconclusive", "EPHEMERAL_RECONCILIATION_REQUIRED"), 3
        else:
            return _result(decision, action, "inconclusive", "EPHEMERAL_RECONCILIATION_REQUIRED",
                           observed), 3
    if observed.action_state in (LifecycleState.TERMINAL.value,
                                 LifecycleState.CLEANUP_PENDING.value):
        try:
            observed = lifecycle.cleanup(exact_id)
        except (CleanupRefused, OSError, RuntimeError, ValueError):
            return _result(decision, action, "inconclusive", "EPHEMERAL_CLEANUP_INCONCLUSIVE", observed), 3
    if observed.action_state in (LifecycleState.ONLINE.value, LifecycleState.BUSY.value,
                                 LifecycleState.CLEANED.value):
        if action["state"] == "started" and observed.action_state == LifecycleState.CLEANED.value:
            action = _transition(action, "succeeded", now(), "EPHEMERAL_CLEANED",
                                 runner_identity(exact_id))
            store.record_action(action)
        return _result(decision, action, "ok", observed.action_state, observed), 0
    code = "EPHEMERAL_RECONCILIATION_REQUIRED" if observed.action_state.startswith("INCONCLUSIVE") else "EPHEMERAL_IN_PROGRESS"
    return _result(decision, action, "inconclusive" if code.endswith("REQUIRED") else "ok",
                   code, observed), 3 if code.endswith("REQUIRED") else 0
