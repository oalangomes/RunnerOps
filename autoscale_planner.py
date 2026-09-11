#!/usr/bin/env python3
"""Deterministic, read-only autoscale planner. Public entrypoint: runnerctl autoscale plan."""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import capacity
from autoscale_contracts import AuditError, canonical_repo, label_list, timestamp

SCHEMA_VERSION = 1
REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
DECISIONS = (
    "WAIT",
    "START_LOCAL",
    "PROVISION_LOCAL",
    "BURST_CLOUD",
    "HOLD",
    "BLOCKED",
    "INCONCLUSIVE",
)
ACTION_DECISIONS = ("START_LOCAL", "PROVISION_LOCAL", "BURST_CLOUD")


class PolicyError(Exception):
    def __init__(self, code="invalid_policy"):
        self.code = code
        super().__init__(code)


def _integer_env(name, default, minimum=0, maximum=1000000):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise PolicyError() from None
    if not minimum <= value <= maximum:
        raise PolicyError()
    return value


def _optional_percent(name):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise PolicyError() from None
    if not 0 <= value <= 100:
        raise PolicyError()
    return value


def _boolean_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise PolicyError()


def _label_scope():
    raw = os.environ.get("RUNNER_AUTOSCALE_LABEL_SCOPE", "").strip()
    if not raw:
        return []
    values = [value.strip() for value in raw.split(",")]
    if any(not value for value in values):
        raise PolicyError()
    try:
        return label_list(values)
    except AuditError:
        raise PolicyError() from None


def load_policy():
    return {
        "queue_threshold_seconds": _integer_env(
            "RUNNER_AUTOSCALE_QUEUE_THRESHOLD_SECONDS", 300, 0, 86400 * 30
        ),
        "max_active_local_runners": _integer_env(
            "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS", 1, 0, 10000
        ),
        "min_memory_available_mib": _integer_env(
            "RUNNER_AUTOSCALE_MIN_MEMORY_AVAILABLE_MIB", 1024, 0, 1024 * 1024
        ),
        "max_cpu_percent": _optional_percent("RUNNER_AUTOSCALE_MAX_CPU_PERCENT"),
        "max_burst_runners": _integer_env(
            "RUNNER_AUTOSCALE_MAX_BURST_RUNNERS", 0, 0, 10000
        ),
        "cooldown_seconds": _integer_env(
            "RUNNER_AUTOSCALE_COOLDOWN_SECONDS", 300, 0, 86400 * 30
        ),
        "burst_enabled": _boolean_env("RUNNER_AUTOSCALE_BURST_ENABLED", False),
        "label_scope": _label_scope(),
    }


def policy_fingerprint(policy):
    payload = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _memory_available_mib(path=Path("/proc/meminfo")):
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) == 3 and parts[2] == "kB":
                    value = int(parts[1])
                    return value // 1024 if value >= 0 else None
    except (OSError, UnicodeError, ValueError):
        return None
    return None


def _cpu_times(path=Path("/proc/stat")):
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0].split()
        if not first or first[0] != "cpu" or len(first) < 5:
            return None
        values = [int(value) for value in first[1:]]
        if any(value < 0 for value in values):
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


def _cpu_percent(sample_seconds=0.05):
    before = _cpu_times()
    if before is None:
        return None
    time.sleep(sample_seconds)
    after = _cpu_times()
    if after is None:
        return None
    total_delta = after[0] - before[0]
    idle_delta = after[1] - before[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return None
    return round(100.0 * (total_delta - idle_delta) / total_delta, 2)


def collect_host_facts(policy):
    memory = _memory_available_mib()
    cpu = _cpu_percent() if policy["max_cpu_percent"] is not None else None
    complete = memory is not None and (
        policy["max_cpu_percent"] is None or cpu is not None
    )
    return {
        "status": "complete" if complete else "inconclusive",
        "memory_available_mib": memory,
        "cpu_percent": cpu,
    }


def load_audit_evidence(repository):
    try:
        from autoscale_store import AuditStore

        with AuditStore() as store:
            evidence = store.planner_evidence(repository)
        return {"status": "complete", **evidence}
    except ImportError:
        return {
            "status": "inconclusive",
            "error": "sqlite_capability_unavailable",
            "queue": [],
            "active_burst_capacity": None,
            "last_scaling_action_started_at": None,
        }
    except AuditError as exc:
        status = "missing" if exc.code == "store_missing" else "inconclusive"
        return {
            "status": status,
            "error": exc.code,
            "queue": [],
            "active_burst_capacity": None,
            "last_scaling_action_started_at": None,
        }


def _label_set(values):
    return {value.casefold() for value in values}


def _self_hosted_jobs(snapshot, policy):
    jobs = snapshot.get("queue", {}).get("jobs")
    if not isinstance(jobs, list):
        return None, None
    self_hosted = []
    for job in jobs:
        labels = job.get("required_labels")
        if not isinstance(labels, list) or not labels or any(not isinstance(label, str) for label in labels):
            return None, None
        normalized = _label_set(labels)
        if "self-hosted" in normalized:
            self_hosted.append(job)
    if not policy["label_scope"]:
        return self_hosted, self_hosted
    wanted = _label_set(policy["label_scope"])
    scoped = [job for job in self_hosted if wanted <= _label_set(job["required_labels"])]
    return self_hosted, scoped


def _snapshot_capacity(snapshot):
    counts = snapshot.get("capacity", {}).get("counts", {})
    result = {}
    for key in ("available_now", "busy_capacity", "provisioned_idle", "inconclusive"):
        value = counts.get(key)
        result[key] = value if type(value) is int and value >= 0 else None
    active = snapshot.get("host", {}).get("active_local_runner_count")
    result["active_local_runner_count"] = active if type(active) is int and active >= 0 else None
    return result


def _queue_identity(job):
    values = (job.get("run_id"), job.get("run_attempt"), job.get("job_id"))
    if any(type(value) is not int or value <= 0 for value in values):
        return None
    return values


def _normalized_queue_evidence(scoped_jobs, audit, observed_at):
    if audit.get("status") != "complete":
        return None
    rows = audit.get("queue")
    if not isinstance(rows, list):
        return None
    by_identity = {}
    for row in rows:
        identity = _queue_identity(row)
        if identity is None or identity in by_identity:
            return None
        by_identity[identity] = row

    result = []
    for job in scoped_jobs:
        identity = _queue_identity(job)
        if identity is None:
            return None
        row = by_identity.get(identity)
        if row is None or row.get("continuous_queued") is not True:
            return None
        try:
            first = timestamp(row["first_seen_queued_at"])
            last = timestamp(row["last_seen_queued_at"])
            if first > last or last > observed_at:
                return None
            current_labels = job.get("required_labels")
            stored_labels = row.get("required_labels")
            if not isinstance(stored_labels, list) or _label_set(current_labels) != _label_set(stored_labels):
                return None
            queued_seconds = int(
                (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
            )
            if queued_seconds < 0:
                return None
            result.append(
                {
                    "job_id": identity[2],
                    "run_id": identity[0],
                    "run_attempt": identity[1],
                    "first_seen_queued_at": first,
                    "last_seen_queued_at": last,
                    "continuous_queued": True,
                    "required_labels": sorted(set(stored_labels)),
                    "github_created_at": (
                        timestamp(row["github_created_at"])
                        if row.get("github_created_at") is not None
                        else None
                    ),
                    "observed_queued_seconds": queued_seconds,
                }
            )
        except (AuditError, KeyError, TypeError, ValueError):
            return None
    return sorted(result, key=lambda row: (row["run_id"], row["run_attempt"], row["job_id"]))


def _idle_target(scoped_jobs, snapshot):
    runners = snapshot.get("capacity", {}).get("runners", [])
    categories = {
        runner.get("name"): runner.get("category")
        for runner in runners
        if isinstance(runner, dict) and isinstance(runner.get("name"), str)
    }
    candidates = set()
    for job in scoped_jobs:
        matching = job.get("matching_local_runner_names")
        if not isinstance(matching, list):
            return None, False
        for name in matching:
            if not isinstance(name, str):
                return None, False
            if categories.get(name) == "provisioned_idle":
                candidates.add(name)
    return (sorted(candidates)[0] if candidates else None), True


def _plan_id(repository, policy, evidence):
    basis = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "policy": policy,
        "evidence": evidence,
    }
    payload = json.dumps(basis, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "plan-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _build_result(repository, observed_at, policy, evidence, decision, reasons, action=None):
    if decision not in DECISIONS:
        raise ValueError("unknown decision")
    normalized_reasons = sorted(set(reasons))
    requested_delta = 1 if decision in ACTION_DECISIONS else 0
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "AutoscalePlan",
        "status": "inconclusive" if decision == "INCONCLUSIVE" else "ok",
        "decision_id": _plan_id(repository, policy, evidence),
        "timestamp": observed_at,
        "repository": repository,
        "policy_fingerprint": policy_fingerprint(policy),
        "decision": decision,
        "reason_codes": normalized_reasons,
        "requested_capacity_delta": requested_delta,
        "action": action,
        "evidence": evidence,
    }
    return result


def plan(snapshot, policy, host, audit):
    """Pure planner: identical normalized evidence + policy yields identical output."""
    try:
        observed_at = timestamp(snapshot["observed_at"])
        repository = canonical_repo(snapshot["repository"]["nameWithOwner"])
    except (AuditError, KeyError, TypeError):
        repository = "unknown/unknown"
        try:
            observed_at = timestamp(snapshot.get("observed_at"))
        except AuditError:
            observed_at = "1970-01-01T00:00:00.000000+00:00"
        evidence = {
            "observed_at": observed_at,
            "queue_status": "inconclusive",
            "queued_job_count": None,
            "queue": [],
            "capacity": {
                "available_now": None,
                "busy_capacity": None,
                "provisioned_idle": None,
                "inconclusive": None,
                "active_local_runner_count": None,
            },
            "active_burst_capacity": None,
            "host": host,
            "scope": {"labels": policy["label_scope"], "scoped_queued_job_count": None, "scoped_job_ids": []},
            "audit": {"status": audit.get("status", "inconclusive"), "last_scaling_action_started_at": None},
        }
        return _build_result(repository, observed_at, policy, evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])

    queue = snapshot.get("queue", {})
    sources = snapshot.get("sources", {})
    queue_count = queue.get("queued_job_count") if type(queue.get("queued_job_count")) is int else None
    capacity_evidence = _snapshot_capacity(snapshot)
    audit_summary = {
        "status": audit.get("status", "inconclusive"),
        "last_scaling_action_started_at": audit.get("last_scaling_action_started_at"),
    }
    base_evidence = {
        "observed_at": observed_at,
        "queue_status": queue.get("status") if queue.get("status") in ("complete", "inconclusive") else "inconclusive",
        "queued_job_count": queue_count,
        "queue": [],
        "capacity": capacity_evidence,
        "active_burst_capacity": audit.get("active_burst_capacity"),
        "host": host,
        "scope": {"labels": policy["label_scope"], "scoped_queued_job_count": None, "scoped_job_ids": []},
        "audit": audit_summary,
    }

    if (
        sources.get("repository") != "complete"
        or sources.get("queue") != "complete"
        or queue.get("status") != "complete"
        or queue_count is None
    ):
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])

    self_hosted, scoped_jobs = _self_hosted_jobs(snapshot, policy)
    if self_hosted is None:
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])

    base_evidence["scope"] = {
        "labels": policy["label_scope"],
        "scoped_queued_job_count": len(scoped_jobs),
        "scoped_job_ids": sorted(job["job_id"] for job in scoped_jobs if type(job.get("job_id")) is int),
    }

    if not scoped_jobs:
        if policy["label_scope"] and self_hosted:
            return _build_result(repository, observed_at, policy, base_evidence, "BLOCKED", ["LABEL_SCOPE_BLOCKED"])
        return _build_result(repository, observed_at, policy, base_evidence, "WAIT", ["NO_SCOPED_QUEUED_WORK"])

    if sources.get("local") != "complete" or sources.get("github_runners") != "complete":
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if snapshot.get("host", {}).get("status") != "complete":
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if any(job.get("capacity_status") == "inconclusive" for job in scoped_jobs):
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if any(job.get("capacity_status") not in (
        "available_now", "busy_capacity", "provisioned_idle", "no_matching_capacity"
    ) for job in scoped_jobs):
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])

    if any(job.get("capacity_status") == "available_now" for job in scoped_jobs):
        return _build_result(
            repository,
            observed_at,
            policy,
            base_evidence,
            "WAIT",
            ["MATCHING_LOCAL_RUNNER_AVAILABLE"],
        )

    observed_queue = _normalized_queue_evidence(scoped_jobs, audit, observed_at)
    if observed_queue is None:
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    base_evidence["queue"] = [
        {key: value for key, value in row.items() if key != "observed_queued_seconds"}
        for row in observed_queue
    ]
    oldest = max(row["observed_queued_seconds"] for row in observed_queue)
    base_evidence["scope"]["oldest_observed_queued_seconds"] = oldest
    base_evidence["scope"]["queue_threshold_seconds"] = policy["queue_threshold_seconds"]

    if oldest < policy["queue_threshold_seconds"]:
        return _build_result(repository, observed_at, policy, base_evidence, "WAIT", ["QUEUE_BELOW_THRESHOLD"])

    reasons = ["OBSERVED_QUEUE_THRESHOLD_MET"]

    last_started = audit.get("last_scaling_action_started_at")
    if last_started is not None:
        try:
            normalized_last_started = timestamp(last_started)
            elapsed = int(
                (datetime.fromisoformat(observed_at) - datetime.fromisoformat(normalized_last_started)).total_seconds()
            )
        except (AuditError, TypeError, ValueError):
            return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
        if elapsed < 0:
            return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
        base_evidence["audit"]["last_scaling_action_started_at"] = normalized_last_started
        base_evidence["audit"]["cooldown_elapsed_seconds"] = elapsed
        if policy["cooldown_seconds"] > 0 and elapsed < policy["cooldown_seconds"]:
            return _build_result(repository, observed_at, policy, base_evidence, "HOLD", reasons + ["COOLDOWN_ACTIVE"])

    if host.get("status") != "complete" or host.get("memory_available_mib") is None:
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if host["memory_available_mib"] < policy["min_memory_available_mib"]:
        return _build_result(repository, observed_at, policy, base_evidence, "HOLD", reasons + ["HOST_MEMORY_HEADROOM_LOW"])
    if policy["max_cpu_percent"] is not None:
        if host.get("cpu_percent") is None:
            return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
        if host["cpu_percent"] > policy["max_cpu_percent"]:
            return _build_result(repository, observed_at, policy, base_evidence, "HOLD", reasons + ["HOST_CPU_THRESHOLD_EXCEEDED"])

    idle_target, idle_evidence_valid = _idle_target(scoped_jobs, snapshot)
    if not idle_evidence_valid:
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if idle_target is not None:
        return _build_result(
            repository,
            observed_at,
            policy,
            base_evidence,
            "START_LOCAL",
            reasons + ["MATCHING_LOCAL_RUNNER_IDLE"],
            {"kind": "START_LOCAL", "target": idle_target},
        )

    active_local = capacity_evidence["active_local_runner_count"]
    if active_local is None:
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if active_local < policy["max_active_local_runners"]:
        return _build_result(
            repository,
            observed_at,
            policy,
            base_evidence,
            "PROVISION_LOCAL",
            reasons + ["LOCAL_POOL_BELOW_MAX"],
            {"kind": "PROVISION_LOCAL", "target": repository},
        )

    reasons.extend(["LOCAL_POOL_AT_MAX", "LOCAL_CAPACITY_SATURATED"])
    if not policy["burst_enabled"]:
        return _build_result(repository, observed_at, policy, base_evidence, "BLOCKED", reasons + ["BURST_DISABLED"])

    active_burst = audit.get("active_burst_capacity")
    if type(active_burst) is not int or active_burst < 0:
        return _build_result(repository, observed_at, policy, base_evidence, "INCONCLUSIVE", ["EVIDENCE_INCONCLUSIVE"])
    if active_burst >= policy["max_burst_runners"]:
        return _build_result(repository, observed_at, policy, base_evidence, "HOLD", reasons + ["BURST_LIMIT_REACHED"])

    return _build_result(
        repository,
        observed_at,
        policy,
        base_evidence,
        "BURST_CLOUD",
        reasons,
        {"kind": "BURST_CLOUD", "target": repository},
    )


def render(result):
    print(f"Autoscale plan: {result['repository']} ({result['status']})")
    print(f"Decision: {result['decision']} id={result['decision_id']}")
    print(f"Reasons: {', '.join(result['reason_codes'])}")
    print(f"Policy: {result['policy_fingerprint']}")
    print(f"Requested capacity delta: {result['requested_capacity_delta']}")
    if result["action"] is not None:
        print(f"Action: {result['action']['kind']} target={result['action']['target']} (planned only)")
    scope = result["evidence"].get("scope", {})
    print(
        "Queue: scoped={} observed={}s threshold={}s".format(
            scope.get("scoped_queued_job_count"),
            scope.get("oldest_observed_queued_seconds", "unknown"),
            scope.get("queue_threshold_seconds", "unknown"),
        )
    )
    print("Read-only plan: no runner lifecycle, provisioning, cloud, or audit-store mutation was performed.")


def main():
    parser = argparse.ArgumentParser(prog="runnerctl autoscale plan", description=__doc__)
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.repository != "." and not re.fullmatch(REPO_PATTERN, args.repository):
        parser.error("expected owner/repo or .")

    try:
        policy = load_policy()
    except PolicyError as exc:
        if args.json:
            print(json.dumps({"schema_version": 1, "kind": "AutoscalePlanError", "status": "error", "error": exc.code}, sort_keys=True))
        else:
            print(f"Autoscale plan: {exc.code}", file=sys.stderr)
        return 2

    snapshot = capacity.snapshot(args.repository)
    repository = snapshot.get("repository", {}).get("nameWithOwner")
    host = collect_host_facts(policy)
    audit = load_audit_evidence(repository) if repository else {
        "status": "inconclusive",
        "error": "canonical_identity_unavailable",
        "queue": [],
        "active_burst_capacity": None,
        "last_scaling_action_started_at": None,
    }
    result = plan(snapshot, policy, host, audit)
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        render(result)
    return 3 if result["decision"] == "INCONCLUSIVE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
