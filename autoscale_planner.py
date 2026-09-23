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
from autoscale_runtime import read_planner_evidence
from autoscale_provision import (
    ProvisionPolicyError,
    load_provision_policy,
    provisioning_candidate,
)

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
    try:
        local_provision = load_provision_policy()
    except ProvisionPolicyError:
        raise PolicyError() from None
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
        "local_scale_out_cooldown_seconds": _integer_env(
            "RUNNER_AUTOSCALE_LOCAL_SCALE_OUT_COOLDOWN_SECONDS", 30, 0, 86400 * 30
        ),
        "burst_enabled": _boolean_env("RUNNER_AUTOSCALE_BURST_ENABLED", False),
        "label_scope": _label_scope(),
        "local_provision": local_provision,
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
        "aggregate_pressure": [],
        "active_burst_capacity": None,
        "last_scaling_action_started_at": None,
        "last_scaling_action_kind": None,
    }


def load_audit_evidence(repository):
    """Read only the retained facts needed by the planner; never create the store."""
    try:
        from autoscale_store import AuditStore

        with AuditStore() as store:
            return read_planner_evidence(store, repository)
    except ImportError:
        return _empty_audit("inconclusive", "sqlite_capability_unavailable")
    except AuditError as exc:
        return _empty_audit(
            "missing" if exc.code == "store_missing" else "inconclusive", exc.code
        )


def _label_set(values):
    return {value.casefold() for value in values}


def _normalized_label_scope(values):
    """Return the exact case-insensitive capability boundary for one job."""
    if not isinstance(values, list) or not values or any(
        not isinstance(value, str) or not value for value in values
    ):
        return None
    return tuple(sorted(_label_set(values)))


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


def _audit_queue_row(row, observed_dt):
    """Normalize one retained episode without trusting GitHub creation time."""
    identity = _queue_identity(row)
    if identity is None:
        return None
    try:
        first = timestamp(row["first_seen_queued_at"])
        last = timestamp(row["last_seen_queued_at"])
        first_dt = datetime.fromisoformat(first)
        last_dt = datetime.fromisoformat(last)
        ended_at = timestamp(row["ended_at"]) if row.get("ended_at") is not None else None
        ended_dt = datetime.fromisoformat(ended_at) if ended_at is not None else None
        labels = row.get("required_labels")
        if (
            first_dt > last_dt
            or last_dt > observed_dt
            or (ended_dt is not None and (last_dt > ended_dt or ended_dt > observed_dt))
            or not isinstance(labels, list)
        ):
            return None
        created_at = (
            timestamp(row["github_created_at"])
            if row.get("github_created_at") is not None
            else None
        )
    except (AuditError, KeyError, TypeError, ValueError):
        return None
    max_gap_seconds = row.get("max_gap_seconds")
    if type(max_gap_seconds) is not int or max_gap_seconds <= 0:
        max_gap_seconds = None
    return {
        "job_id": identity[2],
        "run_id": identity[0],
        "run_attempt": identity[1],
        "first_seen_queued_at": first,
        "last_seen_queued_at": last,
        "continuous_queued": row.get("continuous_queued") is True,
        "ended_at": ended_at,
        "required_labels": sorted(set(labels)),
        "github_created_at": created_at,
        "observed_queued_seconds": int((last_dt - first_dt).total_seconds()),
        "max_gap_seconds": max_gap_seconds,
    }


def _queue_evidence(jobs, audit, observed_at):
    """Return exact current-job observations without letting a new job erase scope evidence."""
    if audit.get("status") != "complete" or not isinstance(audit.get("queue"), list):
        return None

    observed_dt = datetime.fromisoformat(observed_at)
    by_identity = {}
    for row in audit["queue"]:
        normalized = _audit_queue_row(row, observed_dt)
        if normalized is None:
            return None
        identity = (normalized["run_id"], normalized["run_attempt"], normalized["job_id"])
        if normalized["continuous_queued"]:
            if identity in by_identity:
                return None
            by_identity[identity] = normalized

    result = []
    for job in jobs:
        identity = _queue_identity(job)
        row = by_identity.get(identity) if identity is not None else None
        if row is None:
            continue
        if _label_set(job["required_labels"]) != _label_set(row["required_labels"]):
            return None
        result.append(row)

    return sorted(
        result, key=lambda row: (row["run_id"], row["run_attempt"], row["job_id"])
    )


def _legacy_aggregate_queue_evidence(jobs, audit, observed_at):
    """Compatibility for frozen pure-planner fixtures that predate audit schema v2."""
    if audit.get("status") != "complete" or not isinstance(audit.get("queue"), list):
        return None
    try:
        observed_dt = datetime.fromisoformat(observed_at)
        current_by_scope = {}
        for job in jobs:
            scope = _normalized_label_scope(job["required_labels"])
            identity = _queue_identity(job)
            if scope is None or identity is None:
                return None
            current_by_scope.setdefault(scope, set()).add(identity)
    except (KeyError, TypeError, ValueError):
        return None
    if not current_by_scope:
        return None

    episodes = {scope: [] for scope in current_by_scope}
    for row in audit["queue"]:
        normalized = _audit_queue_row(row, observed_dt)
        if normalized is None:
            return None
        scope = _normalized_label_scope(normalized["required_labels"])
        if scope in episodes:
            episodes[scope].append(normalized)

    result = []
    for scope, rows in episodes.items():
        current_identities = current_by_scope[scope]
        current = [
            row
            for row in rows
            if row["continuous_queued"]
            and (row["run_id"], row["run_attempt"], row["job_id"]) in current_identities
        ]
        if not current:
            continue

        persisted_dt = max(datetime.fromisoformat(row["last_seen_queued_at"]) for row in current)
        if persisted_dt > observed_dt:
            return None
        lag_seconds = int((observed_dt - persisted_dt).total_seconds())
        anchor_rows = [
            row
            for row in current
            if datetime.fromisoformat(row["last_seen_queued_at"]) == persisted_dt
        ]
        if lag_seconds > 0:
            gaps = [row["max_gap_seconds"] for row in anchor_rows]
            if not gaps or any(gap is None for gap in gaps) or lag_seconds > min(gaps):
                continue

        merged = []
        for row in sorted(
            rows,
            key=lambda item: (
                item["first_seen_queued_at"],
                item["last_seen_queued_at"],
                item["run_id"],
                item["run_attempt"],
                item["job_id"],
            ),
        ):
            first = datetime.fromisoformat(row["first_seen_queued_at"])
            end_value = (
                row["last_seen_queued_at"]
                if row["continuous_queued"]
                else row["ended_at"] or row["last_seen_queued_at"]
            )
            observed_end = datetime.fromisoformat(end_value)
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], observed_end))
            else:
                merged.append((first, observed_end))
        window = next((item for item in reversed(merged) if item[1] == persisted_dt), None)
        if window is None:
            continue
        result.append(
            {
                "required_labels": list(scope),
                "first_seen_queued_at": timestamp(window[0].isoformat()),
                "last_seen_queued_at": timestamp(persisted_dt.isoformat()),
                "current_observed_at": observed_at,
                "read_only_projection_lag_seconds": lag_seconds,
                "observed_queued_seconds": int((window[1] - window[0]).total_seconds()),
                "current_observed_job_ids": sorted(row["job_id"] for row in current),
                "qualification_id": None,
                "qualification_state": "legacy",
                "resume_count": 0,
                "last_resume_at": None,
                "last_unknown_seconds": None,
                "start_reason": "legacy_fixture",
                "segments": [],
            }
        )
    return sorted(result, key=lambda row: (row["required_labels"], row["first_seen_queued_at"]))


def _aggregate_queue_evidence(jobs, audit, observed_at):
    """Consume durable segmented pressure qualification for each exact label scope."""
    aggregate = audit.get("aggregate_pressure")
    if aggregate is None:
        return _legacy_aggregate_queue_evidence(jobs, audit, observed_at)
    if audit.get("status") != "complete" or not isinstance(aggregate, list):
        return None

    try:
        observed_dt = datetime.fromisoformat(observed_at)
        current_by_scope = {}
        for job in jobs:
            scope = _normalized_label_scope(job["required_labels"])
            identity = _queue_identity(job)
            if scope is None or identity is None:
                return None
            current_by_scope.setdefault(scope, {"identities": set(), "job_ids": []})
            current_by_scope[scope]["identities"].add(identity)
            current_by_scope[scope]["job_ids"].append(job["job_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if not current_by_scope:
        return None

    exact_by_scope = {scope: [] for scope in current_by_scope}
    if not isinstance(audit.get("queue"), list):
        return None
    for raw in audit["queue"]:
        normalized = _audit_queue_row(raw, observed_dt)
        if normalized is None:
            return None
        scope = _normalized_label_scope(normalized["required_labels"])
        if scope in exact_by_scope:
            exact_by_scope[scope].append(normalized)

    result = []
    seen_scopes = set()
    for row in aggregate:
        if not isinstance(row, dict):
            return None
        scope = _normalized_label_scope(row.get("required_labels"))
        if scope not in current_by_scope:
            continue
        if scope in seen_scopes:
            return None
        seen_scopes.add(scope)
        if row.get("state") != "active":
            continue

        try:
            first = timestamp(row["first_observed_at"])
            last = timestamp(row["last_observed_at"])
            first_dt = datetime.fromisoformat(first)
            last_dt = datetime.fromisoformat(last)
            proven = row["proven_queued_seconds"]
            max_gap = row["max_gap_seconds"]
            resume_count = row["resume_count"]
            last_unknown = row.get("last_unknown_seconds")
            segments = row["segments"]
        except (AuditError, KeyError, TypeError, ValueError):
            return None
        if (
            first_dt > last_dt
            or last_dt > observed_dt
            or type(proven) is not int
            or proven < 0
            or type(max_gap) is not int
            or max_gap <= 0
            or type(resume_count) is not int
            or resume_count < 0
            or (last_unknown is not None and (type(last_unknown) is not int or last_unknown < 0))
            or not isinstance(segments, list)
            or not segments
        ):
            return None

        segment_total = 0
        for segment in segments:
            try:
                segment_first = datetime.fromisoformat(timestamp(segment["first_observed_at"]))
                segment_last = datetime.fromisoformat(timestamp(segment["last_observed_at"]))
                segment_seconds = segment["observed_seconds"]
                observation_count = segment["observation_count"]
            except (AuditError, KeyError, TypeError, ValueError):
                return None
            if (
                segment_first > segment_last
                or segment_last > last_dt
                or type(segment_seconds) is not int
                or segment_seconds != int((segment_last - segment_first).total_seconds())
                or type(observation_count) is not int
                or observation_count <= 0
            ):
                return None
            segment_total += segment_seconds
        if segment_total != proven:
            return None

        lag_seconds = int((observed_dt - last_dt).total_seconds())
        if lag_seconds < 0 or lag_seconds > max_gap:
            continue

        if lag_seconds > 0:
            identities = current_by_scope[scope]["identities"]
            anchors = [
                exact
                for exact in exact_by_scope[scope]
                if exact["continuous_queued"]
                and (exact["run_id"], exact["run_attempt"], exact["job_id"]) in identities
                and datetime.fromisoformat(exact["last_seen_queued_at"]) == last_dt
            ]
            if not anchors:
                continue
            gaps = [anchor["max_gap_seconds"] for anchor in anchors]
            if any(gap is None for gap in gaps) or lag_seconds > min(gaps):
                continue

        result.append(
            {
                "required_labels": list(scope),
                "first_seen_queued_at": first,
                "last_seen_queued_at": last,
                "current_observed_at": observed_at,
                "read_only_projection_lag_seconds": lag_seconds,
                "observed_queued_seconds": proven,
                "current_observed_job_ids": sorted(current_by_scope[scope]["job_ids"]),
                "qualification_id": row.get("qualification_id"),
                "qualification_state": row.get("state"),
                "resume_count": resume_count,
                "last_resume_at": row.get("last_resume_at"),
                "last_unknown_seconds": last_unknown,
                "start_reason": row.get("start_reason"),
                "segments": segments,
            }
        )
    return sorted(result, key=lambda item: (item["required_labels"], item["first_seen_queued_at"]))


def _local_capacity_evidence(scoped_jobs, pressure_jobs, snapshot):
    categories = {
        runner.get("name"): runner.get("category")
        for runner in snapshot.get("capacity", {}).get("runners", [])
        if isinstance(runner, dict) and isinstance(runner.get("name"), str)
    }
    matching = {"available_now": set(), "busy_capacity": set(), "provisioned_idle": set()}
    for job in scoped_jobs:
        names = job.get("matching_local_runner_names")
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            return None
        for name in names:
            category = categories.get(name)
            if category in matching:
                matching[category].add(name)
    pressure_names = set()
    for job in pressure_jobs:
        pressure_names.update(job["matching_local_runner_names"])
    return {
        "available_matching_capacity": len(matching["available_now"]),
        "active_matching_local_capacity": len(
            (matching["available_now"] | matching["busy_capacity"]) & pressure_names
        ),
        "provisioned_idle_matching_capacity": len(
            matching["provisioned_idle"] & pressure_names
        ),
        "provisioned_idle_matching_runner_names": sorted(
            matching["provisioned_idle"] & pressure_names
        ),
    }


def _pressure_jobs_from_available_capacity(scoped_jobs, snapshot):
    """Allocate each currently available matching runner to at most one job."""
    runners = snapshot.get("capacity", {}).get("runners")
    if not isinstance(runners, list):
        return None

    categories = {}
    for runner in runners:
        if not isinstance(runner, dict) or not isinstance(runner.get("name"), str):
            return None
        name = runner["name"]
        category = runner.get("category")
        if name in categories or category not in (
            "available_now",
            "busy_capacity",
            "provisioned_idle",
            "inconclusive",
        ):
            return None
        categories[name] = category

    candidates = {}
    for index, job in enumerate(scoped_jobs):
        names = job.get("matching_local_runner_names")
        if not isinstance(names, list) or any(
            not isinstance(name, str) or name not in categories for name in names
        ) or len(set(names)) != len(names):
            return None
        candidates[index] = sorted(
            name for name in names if categories[name] == "available_now"
        )

    ordered_indexes = sorted(
        candidates,
        key=lambda index: (
            scoped_jobs[index].get("job_id")
            if type(scoped_jobs[index].get("job_id")) is int
            else 0,
            scoped_jobs[index].get("run_id")
            if type(scoped_jobs[index].get("run_id")) is int
            else 0,
            index,
        ),
    )
    assigned = {}

    def assign(index, visited):
        for name in candidates[index]:
            if name in visited:
                continue
            visited.add(name)
            previous = assigned.get(name)
            if previous is None or assign(previous, visited):
                assigned[name] = index
                return True
        return False

    for index in ordered_indexes:
        assign(index, set())

    covered = set(assigned.values())
    return [job for index, job in enumerate(scoped_jobs) if index not in covered]


def _plan_id(repository, policy, evidence):
    basis = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "policy": policy,
        "evidence": evidence,
    }
    return "plan-" + hashlib.sha256(_canonical_json(basis).encode("utf-8")).hexdigest()[:32]


def _result(
    repository, observed_at, policy, evidence, decision, reasons, action=None, requested_capacity_delta=None
):
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
        "requested_capacity_delta": (
            (1 if decision in ACTION_DECISIONS else 0)
            if requested_capacity_delta is None
            else requested_capacity_delta
        ),
        "action": action,
        "evidence": evidence,
    }


def _base_evidence(observed_at, snapshot, policy, host, audit):
    queue = snapshot.get("queue", {})
    queue_count = queue.get("queued_job_count")
    if type(queue_count) is not int or queue_count < 0:
        queue_count = None
    provision = policy.get("local_provision") or {}
    template = provision.get("template") or {}
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
            "qualified_pressure_labels": [],
            "qualified_pressure_queued_job_count": None,
            "qualified_pressure_job_ids": [],
            "available_matching_capacity": None,
            "current_active_local_capacity": None,
            "active_matching_local_capacity": None,
            "desired_local_capacity": None,
            "capacity_deficit": None,
            "provisioned_idle_matching_capacity": None,
            "aggregate_sustained_pressure": [],
            "scoped_job_ids": [],
            "pressure_job_ids": [],
            "current_local_pool_size": None,
            "max_local_pool_size": provision.get("max_local_runners"),
            "selected_provisioning_scope": None,
            "provisioning_template_labels": template.get("labels", []),
            "provisioning_target": None,
        },
        "audit": {
            "status": audit.get("status", "inconclusive"),
            "error": audit.get("error"),
            "snapshot_observed_at": observed_at,
            "persisted_queue_observed_at": None,
            "read_only_evidence_lag_seconds": None,
            "last_scaling_action_started_at": audit.get(
                "last_scaling_action_started_at"
            ),
            "last_scaling_action_kind": audit.get("last_scaling_action_kind"),
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

    def decide(decision, *reasons, action=None, requested_capacity_delta=None):
        return _result(
            repository,
            observed_at,
            policy,
            evidence,
            decision,
            reasons,
            action=action,
            requested_capacity_delta=requested_capacity_delta,
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

    pressure_jobs = _pressure_jobs_from_available_capacity(scoped_jobs, snapshot)
    if pressure_jobs is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
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

    aggregate_queue = _aggregate_queue_evidence(pressure_jobs, audit, observed_at)
    if not aggregate_queue:
        return decide("INCONCLUSIVE", "QUEUE_EVIDENCE_NOT_CURRENT")

    evidence["queue"] = [
        {
            key: value
            for key, value in row.items()
            if key not in ("ended_at", "observed_queued_seconds", "max_gap_seconds")
        }
        for row in observed_queue
    ]
    latest_persisted = max(row["last_seen_queued_at"] for row in aggregate_queue)
    evidence["audit"].update(
        {
            "persisted_queue_observed_at": latest_persisted,
            "read_only_evidence_lag_seconds": max(
                row["read_only_projection_lag_seconds"] for row in aggregate_queue
            ),
        }
    )
    oldest = max(row["observed_queued_seconds"] for row in aggregate_queue)
    qualified_scopes = [
        row
        for row in aggregate_queue
        if row["observed_queued_seconds"] >= policy["queue_threshold_seconds"]
    ]
    qualified_labels = {tuple(row["required_labels"]) for row in qualified_scopes}
    qualified_pressure_jobs = [
        job
        for job in pressure_jobs
        if _normalized_label_scope(job["required_labels"]) in qualified_labels
    ]
    evidence["scope"].update(
        {
            "oldest_observed_queued_seconds": oldest,
            "queue_threshold_seconds": policy["queue_threshold_seconds"],
            "aggregate_sustained_pressure": aggregate_queue,
            "qualified_pressure_labels": sorted(
                row["required_labels"] for row in qualified_scopes
            ),
            "qualified_pressure_queued_job_count": len(qualified_pressure_jobs),
            "qualified_pressure_job_ids": sorted(
                job["job_id"] for job in qualified_pressure_jobs
            ),
        }
    )
    if not qualified_pressure_jobs:
        return decide("WAIT", "QUEUE_BELOW_THRESHOLD")

    reasons = ["OBSERVED_QUEUE_THRESHOLD_MET", "SUSTAINED_QUEUE_PRESSURE"]
    if any(row.get("resume_count", 0) > 0 for row in qualified_scopes):
        reasons.append("PRESSURE_QUALIFICATION_RESUMED")

    local_capacity = _local_capacity_evidence(
        qualified_pressure_jobs, qualified_pressure_jobs, snapshot
    )
    active_local = evidence["capacity"]["active_local_runner_count"]
    if local_capacity is None or active_local is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
    desired_local = min(
        policy["max_active_local_runners"], active_local + len(qualified_pressure_jobs)
    )
    capacity_deficit = max(0, desired_local - active_local)
    evidence["scope"].update(
        {
            **local_capacity,
            "current_active_local_capacity": active_local,
            "desired_local_capacity": desired_local,
            "capacity_deficit": capacity_deficit,
        }
    )

    idle_targets = local_capacity["provisioned_idle_matching_runner_names"]
    local_start_candidate = capacity_deficit > 0 and bool(idle_targets)
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
        latest_kind = audit.get("last_scaling_action_kind")
        local_scale_candidate = capacity_deficit > 0
        cooldown = (
            policy["local_scale_out_cooldown_seconds"]
            if latest_kind in ("START_LOCAL", "PROVISION_LOCAL") and local_scale_candidate
            else policy["cooldown_seconds"]
        )
        evidence["audit"].update(
            {
                "last_scaling_action_kind": latest_kind,
                "effective_cooldown_seconds": cooldown,
            }
        )
        if cooldown and elapsed < cooldown:
            stabilization = ["COOLDOWN_ACTIVE"]
            if latest_kind in ("START_LOCAL", "PROVISION_LOCAL") and local_scale_candidate:
                stabilization.append("LOCAL_SCALE_OUT_STABILIZING")
            return decide("HOLD", *(reasons + stabilization))

    if host.get("status") != "complete" or host.get("memory_available_mib") is None:
        return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
    if host["memory_available_mib"] < policy["min_memory_available_mib"]:
        return decide("HOLD", *(reasons + ["HOST_MEMORY_HEADROOM_LOW"]))
    if policy["max_cpu_percent"] is not None:
        if host.get("cpu_percent") is None:
            return decide("INCONCLUSIVE", "EVIDENCE_INCONCLUSIVE")
        if host["cpu_percent"] > policy["max_cpu_percent"]:
            return decide("HOLD", *(reasons + ["HOST_CPU_THRESHOLD_EXCEEDED"]))

    if local_start_candidate:
        return decide(
            "START_LOCAL",
            *(reasons + ["LOCAL_CAPACITY_DEFICIT", "MATCHING_LOCAL_RUNNER_IDLE"]),
            action={"kind": "START_LOCAL", "target": idle_targets[0]},
            requested_capacity_delta=capacity_deficit,
        )

    if capacity_deficit > 0:
        provision_policy = policy.get("local_provision")
        if provision_policy is None:
            # Frozen pure-planner fixtures from before #73 preserve their old
            # recommendation semantics. Production load_policy always supplies
            # the explicit provisioning policy above.
            return decide(
                "PROVISION_LOCAL",
                *(reasons + ["LOCAL_CAPACITY_DEFICIT", "LOCAL_POOL_BELOW_MAX"]),
                action={"kind": "PROVISION_LOCAL", "target": repository},
                requested_capacity_delta=capacity_deficit,
            )

        candidate = provisioning_candidate(
            snapshot,
            provision_policy,
            [row["required_labels"] for row in qualified_scopes],
        )
        evidence["scope"].update(
            {
                "current_local_pool_size": candidate.get("current_local_pool_size"),
                "max_local_pool_size": candidate.get(
                    "max_local_pool_size", provision_policy.get("max_local_runners")
                ),
                "selected_provisioning_scope": candidate.get("selected_scope"),
                "provisioning_template_labels": candidate.get(
                    "template_labels",
                    (provision_policy.get("template") or {}).get("labels", []),
                ),
                "provisioning_target": candidate.get("target"),
            }
        )
        if candidate["status"] == "inconclusive":
            return decide("INCONCLUSIVE", *(reasons + [candidate["reason"]]))
        if candidate["status"] == "candidate":
            return decide(
                "PROVISION_LOCAL",
                *(reasons + ["LOCAL_CAPACITY_DEFICIT", candidate["reason"]]),
                action={"kind": "PROVISION_LOCAL", "target": candidate["target"]},
                requested_capacity_delta=capacity_deficit,
            )
        reasons.extend(
            ["LOCAL_CAPACITY_DEFICIT", "LOCAL_CAPACITY_SATURATED", candidate["reason"]]
        )
    else:
        reasons.extend(["LOCAL_CAPACITY_TARGET_REACHED", "LOCAL_CAPACITY_SATURATED"])
        if policy.get("local_provision") is None:
            # Legacy direct planner callers used max-active as their only local
            # bound and named that state LOCAL_POOL_AT_MAX. Keep that frozen
            # internal contract without leaking the old meaning into production.
            reasons.append("LOCAL_POOL_AT_MAX")

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
    if scope.get("max_local_pool_size") is not None:
        print(
            "Local pool: current={} max={} provision_target={}".format(
                scope.get("current_local_pool_size"),
                scope.get("max_local_pool_size"),
                scope.get("provisioning_target"),
            )
        )
    audit = result["evidence"].get("audit", {})
    if audit.get("read_only_evidence_lag_seconds") is not None:
        print(
            "Evidence: persisted={} snapshot={} lag={}s".format(
                audit.get("persisted_queue_observed_at"),
                audit.get("snapshot_observed_at"),
                audit.get("read_only_evidence_lag_seconds"),
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
