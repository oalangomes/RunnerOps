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
DECISIONS = {
    "WAIT",
    "START_LOCAL",
    "PROVISION_LOCAL",
    "BURST_CLOUD",
    "HOLD",
    "BLOCKED",
    "INCONCLUSIVE",
}
ACTION_DECISIONS = {"START_LOCAL", "PROVISION_LOCAL", "BURST_CLOUD"}
KNOWN_CAPACITY_STATUSES = {
    "available_now",
    "busy_capacity",
    "provisioned_idle",
    "no_matching_capacity",
}


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
    """Load and normalize the intentionally small policy surface."""
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


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def policy_fingerprint(policy):
    return "sha256:" + hashlib.sha256(_canonical_json(policy).encode("utf-8")).hexdigest()


def _memory_available_mib(path=Path("/proc/meminfo")):
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) == 3 and parts[2] == "kB":
                    value = int(parts[1])
                    return value // 1024 if value >= 0 else None
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def _cpu_times(path=Path("/proc/stat")):
    try:
        fields = path.read_text(encoding="utf-8").splitlines()[0].split()
        if not fields or fields[0] != "cpu" or len(fields) < 5:
            return None
        values = [int(value) for value in fields[1:]]
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


def _empty_audit(status, error):
    return {
        "status": status,
        "error": error,
        "queue": [],
        "active_burst_capacity": None,
        "last_scaling_action_started_at": None,
    }


def load_audit_evidence(repository):
    """Read only the retained facts needed by the planner; never create the store."""
    try:
        from autoscale_store import AuditStore

        with AuditStore() as store:
            history = store.history(limit=1000)
            if history.get("truncated"):
                return _empty_audit("inconclusive", "audit_history_truncated")

            queue = [
                row
                for row in history["queue_observations"]
                if row.get("repository", "").casefold() == repository.casefold()
                and row.get("continuous_queued") is True
            ]
            active_burst = 0
            started_at = []
            for decision in history["decisions"]:
                if decision.get("repository", "").casefold() != repository.casefold():
                    continue
                explanation = store.explain(decision["decision_id"])
                for action in explanation["actions"]:
                    if action.get("kind") == "BURST_CLOUD" and action.get("state") in (
                        "planned",
                        "started",
                    ):
                        active_burst += 1
                    if action.get("started_at") is not None:
                        started_at.append(timestamp(action["started_at"]))

        return {
            "status": "complete",
            "error": None,
            "queue": queue,
            "active_burst_capacity": active_burst,
            "last_scaling_action_started_at": max(started_at) if started_at else None,
        }
    except ImportError:
        return _empty_audit("inconclusive", "sqlite_capability_unavailable")
    except AuditError as exc:
        return _empty_audit(
            "missing" if exc.code == "store_missing" else "inconclusive", exc.code
        )


def _label_set(values):
    return {value.casefold() for value in values}


def _self_hosted_jobs(snapshot, policy):
    jobs = snapshot.get("queue", {}).get("jobs")
    if not isinstance(jobs, list):
        return None, None

    self_hosted = []
    for job in jobs:
        labels = job.get("required_labels")
        if (
            not isinstance(labels, list)
            or not labels
            or any(not isinstance(label, str) or not label for label in labels)
        ):
            return None, None
        if "self-hosted" in _label_set(labels):
            self_hosted.append(job)

    if not policy["label_scope"]:
        return self_hosted, self_hosted
    required = _label_set(policy["label_scope"])
    return self_hosted, [
        job for job in self_hosted if required <= _label_set(job["required_labels"])
    ]


def _snapshot_capacity(snapshot):
    counts = snapshot.get("capacity", {}).get("counts", {})
    result = {}
    for key in ("available_now", "busy_capacity", "provisioned_idle", "inconclusive"):
        value = counts.get(key)
        result[key] = value if type(value) is int and value >= 0 else None
    active = snapshot.get("host", {}).get("active_local_runner_count")
    result["active_local_runner_count"] = (
        active if type(active) is int and active >= 0 else None
    )
    return result


def _queue_identity(value):
    identity = (value.get("run_id"), value.get("run_attempt"), value.get("job_id"))
    if any(type(item) is not int or item <= 0 for item in identity):
        return None
    return identity


def _queue_evidence(jobs, audit, observed_at):
    """Join current queued jobs to continuous RunnerOps observations by exact attempt."""
    if audit.get("status") != "complete" or not isinstance(audit.get("queue"), list):
        return None

    by_identity = {}
    for row in audit["queue"]:
        identity = _queue_identity(row)
        if identity is None or identity in by_identity:
            return None
        by_identity[identity] = row

    observed_dt = datetime.fromisoformat(observed_at)
    result = []
    for job in jobs:
        identity = _queue_identity(job)
        row = by_identity.get(identity) if identity is not None else None
        if row is None or row.get("continuous_queued") is not True:
            return None
        try:
            first = timestamp(row["first_seen_queued_at"])
            last = timestamp(row["last_seen_queued_at"])
            first_dt = datetime.fromisoformat(first)
            last_dt = datetime.fromisoformat(last)
            labels = row.get("required_labels")
            if (
                first_dt > last_dt
                or last_dt > observed_dt
                or not isinstance(labels, list)
                or _label_set(job["required_labels"]) != _label_set(labels)
            ):
                return None
            duration = int((last_dt - first_dt).total_seconds())
            created_at = (
                timestamp(row["github_created_at"])
                if row.get("github_created_at") is not None
                else None
            )
        except (AuditError, KeyError, TypeError, ValueError):
            return None

        result.append(
            {
                "job_id": identity[2],
                "run_id": identity[0],
                "run_attempt": identity[1],
                "first_seen_queued_at": first,
                "last_seen_queued_at": last,
                "continuous_queued": True,
                "required_labels": sorted(set(labels)),
                "github_created_at": created_at,
                "observed_queued_seconds": duration,
            }
        )

    return sorted(
        result, key=lambda row: (row["run_id"], row["run_attempt"], row["job_id"])
    )


def _idle_target(jobs, snapshot):
    categories = {
        runner.get("name"): runner.get("category")
        for runner in snapshot.get("capacity", {}).get("runners", [])
        if isinstance(runner, dict) and isinstance(runner.get("name"), str)
    }
    candidates = set()
    for job in jobs:
        names = job.get("matching_local_runner_names")
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            return None, False
        candidates.update(
            name for name in names if categories.get(name) == "provisioned_idle"
        )
    return (sorted(candidates)[0] if candidates else None), True


def _plan_id(repository, policy, evidence):
    basis = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "policy": policy,
        "evidence": evidence,
    }
    return "plan-" + hashlib.sha256(_canonical_json(basis).encode("utf-8")).hexdigest()[:32]


def _result(repository, observed_at, policy, evidence, decision, reasons, action=None):
    if decision not in DECISIONS:
        raise ValueError("unknown decision")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "AutoscalePlan",
        "status": "inconclusive" if decision == "INCONCLUSIVE" else "ok",
        "decision_id": _plan_id(repository, policy, evidence),
        "timestamp": observed_at,
        "repository": repository,
        "policy_fingerprint": policy_fingerprint(policy),
        "decision": decision,
        "reason_codes": sorted(set(reasons)),
        "requested_capacity_delta": 1 if decision in ACTION_DECISIONS else 0,
        "action": action,
        "evidence": evidence,
    }


def _base_evidence(observed_at, snapshot, policy, host, audit):
    queue = snapshot.get("queue", {})
    queue_count = queue.get("queued_job_count")
    if type(queue_count) is not int or queue_count < 0:
        queue_count = None
    return {
        "observed_at": observed_at,
        "queue_status": (
            queue.get("status")
            if queue.get("status") in ("complete", "inconclusive")
            else "inconclusive"
        ),
        "queued_job_count": queue_count,
        "queue": [],
        "capacity": _snapshot_capacity(snapshot),
        "active_burst_capacity": audit.get("active_burst_capacity"),
        "host": host,
        "scope": {
            "labels": policy["label_scope"],
            "scoped_queued_job_count": None,
            "pressure_queued_job_count": None,
            "scoped_job_ids": [],
            "pressure_job_ids": [],
        },
        "audit": {
            "status": audit.get("status", "inconclusive"),
            "error": audit.get("error"),
            "last_scaling_action_started_at": audit.get(
                "last_scaling_action_started_at"
            ),
        },
    }


def plan(snapshot, policy, host, audit):
    """Pure planner: identical normalized evidence + policy yields identical output."""
    try:
        observed_at = timestamp(snapshot["observed_at"])
    except (AuditError, KeyError, TypeError):
        observed_at = "1970-01-01T00:00:00.000000+00:00"

    raw_repository = snapshot.get("repository", {})
    try:
        repository = canonical_repo(raw_repository["nameWithOwner"])
    except (AuditError, KeyError, TypeError):
        candidate = raw_repository.get("match_key")
        repository = candidate if isinstance(candidate, str) and re.fullmatch(REPO_PATTERN, candidate) else None

    evidence = _base_evidence(observed_at, snapshot, policy, host, audit)

    def decide(decision, *reasons, action=None):
        return _result(
            repository, observed_at, policy, evidence, decision, reasons, action=action
        )

    sources = snapshot.get("sources", {})
    queue = snapshot.get("queue", {})
    if (
        repository is None
        or sources.get("repository") != "complete"
        or sources.get("queue") != "complete"
        or queue.get("status") != "complete"
        or evidence["queued_job_count"] is None
    ):
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")

    self_hosted, scoped_jobs = _self_hosted_jobs(snapshot, policy)
    if self_hosted is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")

    evidence["scope"].update(
        {
            "scoped_queued_job_count": len(scoped_jobs),
            "scoped_job_ids": sorted(
                job["job_id"]
                for job in scoped_jobs
                if type(job.get("job_id")) is int
            ),
        }
    )
    if not scoped_jobs:
        if policy["label_scope"] and self_hosted:
            return decide("BLOCKED", "LABEL_SCOPE_BLOCKED")
        return decide("WAIT", "NO_SCOPED_QUEUED_WORK")

    if (
        sources.get("local") != "complete"
        or sources.get("github_runners") != "complete"
        or snapshot.get("host", {}).get("status") != "complete"
    ):
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")

    statuses = [job.get("capacity_status") for job in scoped_jobs]
    if any(status == "inconclusive" or status not in KNOWN_CAPACITY_STATUSES for status in statuses):
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")

    # Available capacity only settles the jobs it actually matches. Other scoped
    # jobs can still represent pressure with different labels/capabilities.
    pressure_jobs = [
        job for job in scoped_jobs if job.get("capacity_status") != "available_now"
    ]
    evidence["scope"].update(
        {
            "pressure_queued_job_count": len(pressure_jobs),
            "pressure_job_ids": sorted(job["job_id"] for job in pressure_jobs),
        }
    )
    if not pressure_jobs:
        return decide("WAIT", "MATCHING_LOCAL_RUNNER_AVAILABLE")

    observed_queue = _queue_evidence(pressure_jobs, audit, observed_at)
    if observed_queue is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")

    evidence["queue"] = [
        {key: value for key, value in row.items() if key != "observed_queued_seconds"}
        for row in observed_queue
    ]
    oldest = max(row["observed_queued_seconds"] for row in observed_queue)
    evidence["scope"].update(
        {
            "oldest_observed_queued_seconds": oldest,
            "queue_threshold_seconds": policy["queue_threshold_seconds"],
        }
    )
    if oldest < policy["queue_threshold_seconds"]:
        return decide("WAIT", "QUEUE_BELOW_THRESHOLD")

    reasons = ["OBSERVED_QUEUE_THRESHOLD_MET"]
    last_started = audit.get("last_scaling_action_started_at")
    if last_started is not None:
        try:
            normalized_last = timestamp(last_started)
            elapsed = int(
                (
                    datetime.fromisoformat(observed_at)
                    - datetime.fromisoformat(normalized_last)
                ).total_seconds()
            )
        except (AuditError, TypeError, ValueError):
            return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
        if elapsed < 0:
            return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
        evidence["audit"].update(
            {
                "last_scaling_action_started_at": normalized_last,
                "cooldown_elapsed_seconds": elapsed,
            }
        )
        if policy["cooldown_seconds"] and elapsed < policy["cooldown_seconds"]:
            return decide("HOLD", *(reasons + ["COOLDOWN_ACTIVE"]))

    if host.get("status") != "complete" or host.get("memory_available_mib") is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
    if host["memory_available_mib"] < policy["min_memory_available_mib"]:
        return decide("HOLD", *(reasons + ["HOST_MEMORY_HEADROOM_LOW"]))
    if policy["max_cpu_percent"] is not None:
        if host.get("cpu_percent") is None:
            return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
        if host["cpu_percent"] > policy["max_cpu_percent"]:
            return decide("HOLD", *(reasons + ["HOST_CPU_THRESHOLD_EXCEEDED"]))

    idle_target, idle_known = _idle_target(pressure_jobs, snapshot)
    if not idle_known:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
    if idle_target is not None:
        return decide(
            "START_LOCAL",
            *(reasons + ["MATCHING_LOCAL_RUNNER_IDLE"]),
            action={"kind": "START_LOCAL", "target": idle_target},
        )

    active_local = evidence["capacity"]["active_local_runner_count"]
    if active_local is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
    if active_local < policy["max_active_local_runners"]:
        return decide(
            "PROVISION_LOCAL",
            *(reasons + ["LOCAL_POOL_BELOW_MAX"]),
            action={"kind": "PROVISION_LOCAL", "target": repository},
        )

    reasons.extend(["LOCAL_POOL_AT_MAX", "LOCAL_CAPACITY_SATURATED"])
    if not policy["burst_enabled"]:
        return decide("BLOCKED", *(reasons + ["BURST_DISABLED"]))

    active_burst = audit.get("active_burst_capacity")
    if type(active_burst) is not int or active_burst < 0:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
    if active_burst >= policy["max_burst_runners"]:
        return decide("HOLD", *(reasons + ["BURST_LIMIT_REACHED"]))

    return decide(
        "BURST_CLOUD",
        *reasons,
        action={"kind": "BURST_CLOUD", "target": repository},
    )


def render(result):
    print(f"Autoscale plan: {result['repository'] or '?'} ({result['status']})")
    print(f"Decision: {result['decision']} id={result['decision_id']}")
    print(f"Reasons: {', '.join(result['reason_codes'])}")
    print(f"Policy: {result['policy_fingerprint']}")
    print(f"Requested capacity delta: {result['requested_capacity_delta']}")
    if result["action"] is not None:
        print(
            f"Action: {result['action']['kind']} target={result['action']['target']} (planned only)"
        )
    scope = result["evidence"].get("scope", {})
    print(
        "Queue: scoped={} pressure={} observed={}s threshold={}s".format(
            scope.get("scoped_queued_job_count"),
            scope.get("pressure_queued_job_count"),
            scope.get("oldest_observed_queued_seconds", "unknown"),
            scope.get("queue_threshold_seconds", "unknown"),
        )
    )
    print(
        "Read-only plan: no runner lifecycle, provisioning, cloud, or audit-store mutation was performed."
    )


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
            print(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "kind": "AutoscalePlanError",
                        "status": "error",
                        "error": exc.code,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"Autoscale plan: {exc.code}", file=sys.stderr)
        return 2

    snapshot = capacity.snapshot(args.repository)
    repository = snapshot.get("repository", {}).get("nameWithOwner")
    audit = (
        load_audit_evidence(repository)
        if repository
        else _empty_audit("inconclusive", "canonical_identity_unavailable")
    )
    result = plan(snapshot, policy, collect_host_facts(policy), audit)
    if args.json:
        print(
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
    else:
        render(result)
    return 3 if result["decision"] == "INCONCLUSIVE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
