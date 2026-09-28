"""Replay-safe controller state machine for one governed PROVISION_LOCAL action.

The main autoscale controller owns collection, planning and the host lock. This
module owns only the mutation-specific action lifecycle and never starts runners.
"""

import hashlib
import json

from autoscale_contracts import action_record, canonical_repo, decision_record, timestamp
from autoscale_provision import provisioned_identity
from autoscale_planner import policy_fingerprint


def _action_id(decision_id, target):
    basis = decision_id + "\0PROVISION_LOCAL\0" + target
    return "action-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def planned_action(plan, at):
    if plan.get("decision") != "PROVISION_LOCAL" or plan.get("action", {}).get("kind") != "PROVISION_LOCAL":
        raise ValueError("not_provision_plan")
    target = plan["action"].get("target")
    if not isinstance(target, str) or not target:
        raise ValueError("invalid_provision_target")
    return action_record(
        {
            "action_id": _action_id(plan["decision_id"], target),
            "decision_id": plan["decision_id"],
            "kind": "PROVISION_LOCAL",
            "target": target,
            "state": "planned",
            "timestamp": timestamp(at),
            "started_at": None,
            "finished_at": None,
            "external_id": None,
            "diagnostic": {"code": "PROVISION_PLANNED", "exit_code": None},
        }
    )


def _transition(action, state, at, code, exit_code=None, external_id=None):
    at = timestamp(at)
    started_at = action["started_at"]
    if state == "started" and started_at is None:
        started_at = at
    finished_at = at if state in ("succeeded", "failed", "cancelled") else None
    if external_id is None:
        external_id = action["external_id"]
    return action_record(
        {
            **action,
            "state": state,
            "timestamp": at,
            "started_at": started_at,
            "finished_at": finished_at,
            "external_id": external_id,
            "diagnostic": {"code": code, "exit_code": exit_code},
        }
    )


def pending_provision_actions(store, repository):
    """Read retained non-terminal provisioning actions for one repository."""

    repository = canonical_repo(repository)
    with store._read_transaction():
        rows = store.connection.execute(
            """SELECT d.payload AS decision_payload, a.payload AS action_payload
            FROM actions a
            JOIN decisions d USING(decision_id)
            WHERE lower(d.repository)=lower(?)
              AND a.state IN ('planned','started')
            ORDER BY a.updated_at, a.action_id""",
            (repository,),
        ).fetchall()
    result = []
    for row in rows:
        decision = decision_record(json.loads(row["decision_payload"]))
        action = action_record(json.loads(row["action_payload"]))
        if action["kind"] == "PROVISION_LOCAL":
            result.append({"decision": decision, "action": action})
    return result


def _target_state(snapshot, target):
    if snapshot.get("sources", {}).get("local") != "complete":
        return "inconclusive", None
    rows = snapshot.get("capacity", {}).get("runners")
    if not isinstance(rows, list):
        return "inconclusive", None
    matches = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("scope") == "local" and row.get("name") == target
    ]
    if not matches:
        return "absent", None
    if len(matches) != 1:
        return "inconclusive", None
    identity = provisioned_identity(snapshot, target)
    if identity is not None:
        return "ready", identity
    return "present", None


def result(decision, action=None, *, status, diagnostic):
    return {
        "status": status,
        "repository": decision.get("repository"),
        "decision_id": decision.get("decision_id"),
        "decision": decision.get("decision"),
        "reason_codes": decision.get("reason_codes", []),
        "action_id": action.get("action_id") if action else None,
        "action_state": action.get("state") if action else None,
        "target": action.get("target") if action else None,
        "diagnostic": diagnostic,
    }


def reconcile_or_apply(
    store,
    pending,
    fresh_snapshot,
    fresh_plan,
    fresh_policy,
    repository,
    *,
    provision_fn,
    snapshot_fn,
    clock,
):
    """Reconcile or cross the add boundary exactly once for one provision action.

    ``pending`` contains the persisted decision/action. A started action is never
    submitted to ``runnerctl add`` again, even when its previous outcome is unknown.
    """

    decision = pending["decision"]
    action = pending["action"]
    now = lambda: timestamp(clock().isoformat())

    if action["state"] == "started":
        state, identity = _target_state(fresh_snapshot, action["target"])
        if state == "ready":
            terminal = _transition(
                action,
                "succeeded",
                now(),
                "PROVISION_VERIFIED_IDLE",
                0,
                external_id=identity,
            )
            store.record_action(terminal)
            return result(decision, terminal, status="ok", diagnostic="PROVISION_VERIFIED_IDLE"), 0
        return result(
            decision,
            action,
            status="inconclusive",
            diagnostic=(
                "PROVISION_TARGET_PRESENT_UNVERIFIED"
                if state == "present"
                else "PROVISION_OUTCOME_UNKNOWN"
            ),
        ), 3

    if action["state"] != "planned":
        raise ValueError("invalid_pending_provision_state")

    if policy_fingerprint(fresh_policy) != decision["policy_fingerprint"]:
        cancelled = _transition(action, "cancelled", now(), "POLICY_CHANGED")
        store.record_action(cancelled)
        return result(decision, cancelled, status="noop", diagnostic="POLICY_CHANGED"), 0

    same_plan = (
        fresh_plan.get("decision") == "PROVISION_LOCAL"
        and fresh_plan.get("action", {}).get("kind") == "PROVISION_LOCAL"
        and fresh_plan.get("action", {}).get("target") == action["target"]
    )
    if not same_plan:
        cancelled = _transition(action, "cancelled", now(), "PLAN_CHANGED")
        store.record_action(cancelled)
        return result(decision, cancelled, status="noop", diagnostic="PLAN_CHANGED"), 0

    state, identity = _target_state(fresh_snapshot, action["target"])
    if state == "ready":
        cancelled = _transition(action, "cancelled", now(), "TARGET_ALREADY_PROVISIONED")
        store.record_action(cancelled)
        return result(
            decision, cancelled, status="noop", diagnostic="TARGET_ALREADY_PROVISIONED"
        ), 0
    if state != "absent":
        return result(
            decision,
            action,
            status="inconclusive",
            diagnostic="PROVISION_TARGET_INCONCLUSIVE",
        ), 3

    started = _transition(action, "started", now(), "PROVISION_REQUESTED")
    store.record_action(started)

    outcome = provision_fn(repository, action["target"], fresh_policy["local_provision"])
    if outcome["status"] == "failed":
        terminal = _transition(
            started,
            "failed",
            now(),
            outcome["code"],
            outcome.get("exit_code"),
        )
        store.record_action(terminal)
        return result(decision, terminal, status="failed", diagnostic=outcome["code"]), 1

    observed = snapshot_fn(repository)
    state, identity = _target_state(observed, action["target"])
    if state == "ready":
        terminal = _transition(
            started,
            "succeeded",
            now(),
            "PROVISION_VERIFIED_IDLE",
            outcome.get("exit_code", 0),
            external_id=identity,
        )
        store.record_action(terminal)
        return result(decision, terminal, status="ok", diagnostic="PROVISION_VERIFIED_IDLE"), 0

    # Any PARTIAL/INCONCLUSIVE or unverifiable success remains started. The
    # immutable started receipt proves the boundary may have been crossed; later
    # iterations reconcile this same target and never call add again blindly.
    diagnostic = (
        outcome["code"]
        if outcome["status"] == "inconclusive"
        else "PROVISION_VERIFICATION_INCONCLUSIVE"
    )
    return result(decision, started, status="inconclusive", diagnostic=diagnostic), 3
