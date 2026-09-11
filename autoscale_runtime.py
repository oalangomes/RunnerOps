"""Targeted read helpers shared by the governed autoscale controller.

This module deliberately stays inside the autoscale boundary. It reads the #69
SQLite schema through an already-open ``AuditStore`` connection and never mutates
state itself.
"""

import json
from datetime import timezone

from autoscale_contracts import (
    AuditError,
    action_record,
    canonical_repo,
    decision_record,
    instant,
    label_list,
    timestamp,
)

MAX_PLANNER_QUEUE_ROWS = 100


def _repo_key(repository):
    return canonical_repo(repository).lower()


def read_planner_evidence(store, repository):
    """Read only the retained facts needed by the planner for one repository.

    Unlike ``history(limit=...)``, these queries are repository-scoped and target
    only open queue episodes, active actions and the latest started-action event.
    Unrelated or old terminal history cannot make controller planning inconclusive.
    """

    repository = canonical_repo(repository)
    repo_key = _repo_key(repository)
    with store._read_transaction():  # Internal autoscale boundary; no public API leak.
        rows = store.connection.execute(
            """SELECT q.*, r.repository
            FROM queue_observations q
            JOIN repository_observations r USING(repo_key)
            WHERE q.repo_key=? AND q.ended_at IS NULL
            ORDER BY q.last_seen_queued_at DESC, q.observation_id
            LIMIT ?""",
            (repo_key, MAX_PLANNER_QUEUE_ROWS + 1),
        ).fetchall()
        if len(rows) > MAX_PLANNER_QUEUE_ROWS:
            raise AuditError("planner_evidence_too_large")

        active_action_rows = store.connection.execute(
            """SELECT a.payload
            FROM actions a
            JOIN decisions d USING(decision_id)
            WHERE lower(d.repository)=lower(?)
              AND a.state IN ('planned','started')
            ORDER BY a.updated_at DESC, a.action_id""",
            (repository,),
        ).fetchall()

        latest_started = store.connection.execute(
            """SELECT e.timestamp
            FROM action_events e
            JOIN actions a USING(action_id)
            JOIN decisions d USING(decision_id)
            WHERE lower(d.repository)=lower(?)
              AND e.state='started'
            ORDER BY e.timestamp DESC, e.action_id DESC
            LIMIT 1""",
            (repository,),
        ).fetchone()

    queue = []
    now = store.clock().astimezone(timezone.utc)
    for row in rows:
        item = dict(row)
        labels = label_list(json.loads(item["required_labels"]))
        fresh = (
            0
            <= (now - instant(item["last_seen_queued_at"])).total_seconds()
            <= item["max_gap_seconds"]
        )
        queue.append(
            {
                "observation_id": item["observation_id"],
                "repository": canonical_repo(item["repository"]),
                "job_id": item["job_id"],
                "run_id": item["run_id"],
                "run_attempt": item["run_attempt"],
                "first_seen_queued_at": timestamp(item["first_seen_queued_at"]),
                "last_seen_queued_at": timestamp(item["last_seen_queued_at"]),
                "continuous_queued": fresh,
                "required_labels": labels,
                "github_created_at": (
                    timestamp(item["github_created_at"])
                    if item["github_created_at"] is not None
                    else None
                ),
                "observed_queued_seconds": int(
                    (
                        instant(item["last_seen_queued_at"])
                        - instant(item["first_seen_queued_at"])
                    ).total_seconds()
                ),
            }
        )

    active_burst = 0
    for row in active_action_rows:
        action = action_record(json.loads(row["payload"]))
        if action["kind"] == "BURST_CLOUD":
            active_burst += 1

    return {
        "status": "complete",
        "error": None,
        "queue": queue,
        "active_burst_capacity": active_burst,
        "last_scaling_action_started_at": (
            timestamp(latest_started["timestamp"]) if latest_started is not None else None
        ),
    }


def pending_start_actions(store, repository, target=None):
    """Return retained non-terminal START_LOCAL actions for replay-safe recovery."""

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
        if action["kind"] != "START_LOCAL":
            continue
        if target is not None and action["target"] != target:
            continue
        result.append({"decision": decision, "action": action})
    return result


def decision_from_plan(plan):
    """Project rich AutoscalePlan evidence into the closed #69 storage contract."""

    evidence = plan["evidence"]
    return decision_record(
        {
            "decision_id": plan["decision_id"],
            "timestamp": plan["timestamp"],
            "repository": plan["repository"],
            "policy_fingerprint": plan["policy_fingerprint"],
            "decision": plan["decision"],
            "reason_codes": plan["reason_codes"],
            "requested_capacity_delta": plan["requested_capacity_delta"],
            "evidence": {
                "observed_at": evidence["observed_at"],
                "queue_status": evidence["queue_status"],
                "queued_job_count": evidence["queued_job_count"],
                "queue": evidence["queue"],
                "capacity": evidence["capacity"],
                "active_burst_capacity": evidence["active_burst_capacity"],
            },
        }
    )
