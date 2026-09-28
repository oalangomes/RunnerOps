#!/usr/bin/env python3
"""Bounded, read-only OperationalEvidence v1 projection."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import re
import sys

import capacity
from autoscale_contracts import AuditError, utcnow
from autoscale_store import AuditStore

REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
CAPABILITY_LIMITATIONS = frozenset({
    "ci_history_not_persisted",
    "historical_capacity_not_persisted",
    "historical_collector_metrics_unavailable",
})
COLLECTOR_FIELDS = (
    "wall_time_ms", "github_calls", "repo_resolution_calls", "run_list_calls",
    "job_list_calls", "runner_list_calls", "job_query_retry_calls",
    "job_query_workers", "repo_cache_hits", "scheduler_interval_seconds",
    "scheduler_headroom_ms", "scheduler_overrun", "scheduler_near_overrun",
    "canonical_source", "cache_scope", "cache_ttl_seconds",
)


def duration(value):
    match = re.fullmatch(r"([1-9]\d{0,5})([smhd])", value)
    if not match:
        raise argparse.ArgumentTypeError("use a positive duration such as 30m, 24h or 7d")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if seconds > 3650 * 86400:
        raise argparse.ArgumentTypeError("duration exceeds 3650d")
    return seconds


def _counts(values):
    return dict(sorted(Counter(values).items()))


def _add_incomplete(items, source, reason):
    item = {"source": source, "reason": reason}
    if item not in items:
        items.append(item)


def _queue_age_summary(jobs):
    ages = sorted(job["queue_age_seconds"] for job in jobs if job.get("queue_age_seconds") is not None)
    if not ages:
        return {"count": 0, "min_seconds": None, "max_seconds": None, "average_seconds": None}
    return {
        "count": len(ages),
        "min_seconds": ages[0],
        "max_seconds": ages[-1],
        "average_seconds": sum(ages) / len(ages),
    }


def _current_capacity(snapshot, incomplete):
    if snapshot is None:
        _add_incomplete(incomplete, "capacity", "current_capacity_unavailable")
        return {"observed_at": None, "status": "inconclusive", "latest": None,
                "queue": None, "historical_utilization": None}
    if snapshot.get("status") != "complete":
        _add_incomplete(incomplete, "capacity", "current_capacity_inconclusive")
    for error in snapshot.get("errors", []):
        if isinstance(error, dict) and isinstance(error.get("source"), str) and isinstance(error.get("reason"), str):
            _add_incomplete(incomplete, error["source"], error["reason"])
    counts = snapshot.get("capacity", {}).get("counts", {})
    return {
        "observed_at": snapshot.get("observed_at"),
        "status": snapshot.get("status"),
        "latest": {key: counts.get(key) for key in (
            "available_now", "busy_capacity", "provisioned_idle", "inconclusive")},
        "queue": {
            "observed_queued_job_count": snapshot.get("queue", {}).get("observed_queued_job_count"),
            "queued_job_count": snapshot.get("queue", {}).get("queued_job_count"),
        },
        "historical_utilization": None,
    }


def _autoscale(history):
    decisions = history.get("decisions", [])
    actions = history.get("actions", [])
    reason_codes = [reason for decision in decisions for reason in decision.get("reason_codes", [])]
    decision_kinds = [decision.get("decision") for decision in decisions]
    action_kinds = [action.get("kind") for action in actions]
    action_states = [action.get("state") for action in actions]
    diagnostic_codes = [action.get("diagnostic", {}).get("code") for action in actions
                        if action.get("diagnostic", {}).get("code")]
    return {
        "decisions": {"observed": len(decisions), "by_kind": _counts(decision_kinds),
                      "reason_codes": _counts(reason_codes)},
        "actions": {"observed": len(actions), "by_kind": _counts(action_kinds),
                    "by_state": _counts(action_states), "diagnostic_codes": _counts(diagnostic_codes)},
        "recovery": {"queue_episode_end_reasons": _counts(
            row["end_reason"] for row in history.get("queue_observations", [])
            if row.get("end_reason"))},
        "truncated": bool(history.get("truncated")),
    }


def _read_history(target, period_from, period_to, store_factory, incomplete):
    if target is None:
        _add_incomplete(incomplete, "autoscale", "audit_history_unavailable")
        return None
    try:
        with store_factory() as store:
            try:
                return store.history(
                    period_from.isoformat(), until=period_to.isoformat(),
                    repository=target, include_actions=True,
                )
            except (AuditError, OSError, TypeError, ValueError):
                _add_incomplete(incomplete, "autoscale", "query_failed")
                return None
    except (AuditError, OSError):
        _add_incomplete(incomplete, "autoscale", "audit_history_unavailable")
        return None


def _read_snapshot(repository, snapshot_fn, incomplete):
    try:
        return snapshot_fn(repository)
    except (capacity.EvidenceError, OSError, TypeError, ValueError):
        _add_incomplete(incomplete, "capacity", "current_capacity_unavailable")
        return None


def _collection_succeeded(incomplete):
    return all(item["reason"] in CAPABILITY_LIMITATIONS for item in incomplete)


def _collector_summary(snapshot):
    metrics = snapshot.get("collector") if snapshot else None
    if not isinstance(metrics, dict):
        return None
    return {key: metrics[key] for key in COLLECTOR_FIELDS if key in metrics}


def build_report(repository, since_seconds, *, now=None, snapshot_fn=None, store_factory=None):
    now = now or utcnow()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    period_from = now - timedelta(seconds=since_seconds)
    requested = repository
    incomplete = []
    snapshot_fn = snapshot_fn or capacity.snapshot
    snapshot = _read_snapshot(requested, snapshot_fn, incomplete)
    canonical = snapshot.get("repository", {}).get("nameWithOwner") if snapshot else None
    if canonical is None:
        _add_incomplete(incomplete, "repository", "canonical_repository_unavailable")
    target = canonical if canonical and re.fullmatch(REPO_PATTERN, canonical) else None
    store_factory = store_factory or AuditStore
    history = _read_history(target, period_from, now, store_factory, incomplete)
    if history and history.get("truncated"):
        _add_incomplete(incomplete, "autoscale", "audit_history_truncated")

    jobs = snapshot.get("queue", {}).get("jobs", []) if snapshot else []
    if snapshot is None or snapshot.get("queue", {}).get("status") != "complete":
        _add_incomplete(incomplete, "ci", "ci_history_incomplete")
    _add_incomplete(incomplete, "ci", "ci_history_not_persisted")
    _add_incomplete(incomplete, "capacity", "historical_capacity_not_persisted")
    _add_incomplete(incomplete, "collector", "historical_collector_metrics_unavailable")
    capacity_summary = _current_capacity(snapshot, incomplete)
    report = {
        "schema_version": 1,
        "kind": "OperationalEvidence",
        "period": {"from": period_from.isoformat(), "to": now.isoformat(),
                   "from_inclusive": True, "to_exclusive": True},
        "repository": {"requested": requested, "nameWithOwner": canonical},
        "ci": {"queued_jobs_observed": len(jobs), "queue_age_seconds": _queue_age_summary(jobs),
               "observed_at": snapshot.get("observed_at") if snapshot else None},
        "capacity": capacity_summary,
        "autoscale": _autoscale(history or {"decisions": [], "actions": [], "queue_observations": []}),
        "collector": _collector_summary(snapshot),
        "collection_status": "success" if _collection_succeeded(incomplete) else "failed",
        "incomplete_evidence": sorted(incomplete, key=lambda item: (item["source"], item["reason"])),
    }
    return report


def render(report):
    repo = report["repository"]["nameWithOwner"] or report["repository"]["requested"]
    print(f"Operational report: {repo}")
    print(f"Period: {report['period']['from']} inclusive to {report['period']['to']} exclusive")
    print("CI")
    print(f"  queued jobs observed: {report['ci']['queued_jobs_observed']}")
    print(f"  queue age samples: {report['ci']['queue_age_seconds']['count']}")
    latest = report["capacity"]["latest"] or {}
    print("Capacity")
    print("  latest: " + ", ".join(f"{key}={latest.get(key)}" for key in (
        "available_now", "busy_capacity", "provisioned_idle", "inconclusive")))
    print("  historical utilization: unavailable")
    decisions = report["autoscale"]["decisions"]
    print("Autoscale")
    print(f"  decisions observed: {decisions['observed']}")
    print(f"  kinds: {json.dumps(decisions['by_kind'], sort_keys=True)}")
    print(f"  incomplete evidence: {len(report['incomplete_evidence'])}")


def main():
    parser = argparse.ArgumentParser(prog="runnerctl report", description=__doc__)
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--since", required=True, type=duration)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.repository != "." and not re.fullmatch(REPO_PATTERN, args.repository):
        parser.error("expected owner/repo or .")
    result = build_report(args.repository, args.since)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        render(result)
    return 0 if result["collection_status"] == "success" else 3


if __name__ == "__main__":
    raise SystemExit(main())
