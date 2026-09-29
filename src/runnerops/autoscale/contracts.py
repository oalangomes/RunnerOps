"""Small, allowlisted persistence contracts; no free-form telemetry payloads."""

import json
import os
import re
from datetime import datetime, timezone

DECISIONS = (
    "WAIT",
    "START_LOCAL",
    "PROVISION_LOCAL",
    "BURST_CLOUD",
    "HOLD",
    "BLOCKED",
    "INCONCLUSIVE",
)
ACTION_STATES = ("planned", "started", "succeeded", "failed", "cancelled")
CAPACITY = ("available_now", "busy_capacity", "provisioned_idle", "inconclusive")
SECRET_KEY = re.compile(r"token|secret|password|credential|authorization|private.?key", re.I)
SECRET_VALUE = re.compile(
    r"gh[pousr]_|github_pat_|(?:AKIA|ASIA)[A-Z0-9]{16}|bearer |private key|eyJ[\w-]+\.eyJ", re.I
)


class AuditError(Exception):
    """Only stable codes are returned: never echo rejected data or raw SQL errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise AuditError("invalid_timestamp") from None


def instant(value):
    return datetime.fromisoformat(timestamp(value))


def integer(value, minimum=0, maximum=1000000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AuditError("invalid_integer")
    return value


def text(value, pattern=r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}"):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise AuditError("invalid_identifier")
    if SECRET_VALUE.search(value):
        raise AuditError("secret_rejected")
    # Catch opaque credentials present in known secret-bearing environment variables.
    # Environment names/values themselves are never serialized or logged.
    if any(
        len(secret) >= 8 and secret in value
        for key, secret in os.environ.items()
        if SECRET_KEY.search(key)
    ):
        raise AuditError("secret_rejected")
    return value


def fields(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise AuditError("invalid_fields")


def canonical_repo(value):
    return text(value, r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def label_list(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 32:
        raise AuditError("invalid_labels")
    return sorted(set(text(label, r"[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,63}") for label in value))


def encode(value):
    data = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    if len(data) > 32768:
        raise AuditError("payload_too_large")
    return data


def queue_evidence(value):
    fields(
        value,
        (
            "job_id",
            "run_id",
            "run_attempt",
            "first_seen_queued_at",
            "last_seen_queued_at",
            "continuous_queued",
        ),
        ("required_labels", "github_created_at"),
    )
    result = {key: integer(value[key], 1, 2**63 - 1) for key in ("job_id", "run_id", "run_attempt")}
    result.update(
        {key: timestamp(value[key]) for key in ("first_seen_queued_at", "last_seen_queued_at")}
    )
    if (
        result["first_seen_queued_at"] > result["last_seen_queued_at"]
        or type(value["continuous_queued"]) is not bool
    ):
        raise AuditError("invalid_queue_evidence")
    result["continuous_queued"] = value["continuous_queued"]
    result["required_labels"] = label_list(value.get("required_labels"))
    result["github_created_at"] = (
        timestamp(value["github_created_at"])
        if value.get("github_created_at") is not None
        else None
    )
    return result


def decision_record(value):
    fields(
        value,
        (
            "decision_id",
            "timestamp",
            "repository",
            "policy_fingerprint",
            "decision",
            "reason_codes",
            "requested_capacity_delta",
            "evidence",
        ),
    )
    if value["decision"] not in DECISIONS:
        raise AuditError("invalid_decision")
    reasons = value["reason_codes"]
    if not isinstance(reasons, list) or not 1 <= len(reasons) <= 16:
        raise AuditError("invalid_reasons")
    evidence = value["evidence"]
    fields(
        evidence,
        (
            "observed_at",
            "queue_status",
            "queued_job_count",
            "queue",
            "capacity",
            "active_burst_capacity",
        ),
    )
    if evidence["queue_status"] not in ("complete", "inconclusive"):
        raise AuditError("invalid_queue_status")
    if not isinstance(evidence["queue"], list) or len(evidence["queue"]) > 100:
        raise AuditError("payload_too_large")
    fields(evidence["capacity"], (*CAPACITY, "active_local_runner_count"))

    def nullable_count(value):
        return None if value is None else integer(value)

    result = {
        "decision_id": text(value["decision_id"]),
        "timestamp": timestamp(value["timestamp"]),
        "repository": canonical_repo(value["repository"]),
        "policy_fingerprint": text(value["policy_fingerprint"], r"sha256:[a-f0-9]{64}"),
        "decision": value["decision"],
        "reason_codes": sorted(set(text(reason, r"[A-Z][A-Z0-9_]{0,63}") for reason in reasons)),
        "requested_capacity_delta": integer(value["requested_capacity_delta"], -10000, 10000),
        "evidence": {
            "observed_at": timestamp(evidence["observed_at"]),
            "queue_status": evidence["queue_status"],
            "queued_job_count": nullable_count(evidence["queued_job_count"]),
            "queue": [queue_evidence(item) for item in evidence["queue"]],
            "capacity": {
                key: nullable_count(evidence["capacity"][key])
                for key in (*CAPACITY, "active_local_runner_count")
            },
            "active_burst_capacity": nullable_count(evidence["active_burst_capacity"]),
        },
    }
    if result["evidence"]["observed_at"] > result["timestamp"] or any(
        item["last_seen_queued_at"] > result["evidence"]["observed_at"]
        for item in result["evidence"]["queue"]
    ):
        raise AuditError("invalid_evidence_time")
    encode(result)
    return result


def action_record(value):
    fields(
        value,
        (
            "action_id",
            "decision_id",
            "kind",
            "target",
            "state",
            "timestamp",
            "started_at",
            "finished_at",
            "external_id",
            "diagnostic",
        ),
    )
    if (
        value["kind"] not in ("START_LOCAL", "PROVISION_LOCAL", "BURST_CLOUD")
        or value["state"] not in ACTION_STATES
    ):
        raise AuditError("invalid_action")
    fields(value["diagnostic"], ("code", "exit_code"))
    result = {key: text(value[key]) for key in ("action_id", "decision_id", "target")}
    result.update({key: value[key] for key in ("kind", "state")})
    result["timestamp"] = timestamp(value["timestamp"])
    for key in ("started_at", "finished_at"):
        result[key] = timestamp(value[key]) if value[key] is not None else None
    result["external_id"] = text(value["external_id"]) if value["external_id"] is not None else None
    result["diagnostic"] = {
        "code": text(value["diagnostic"]["code"], r"[A-Z][A-Z0-9_]{0,63}")
        if value["diagnostic"]["code"] is not None
        else None,
        "exit_code": integer(value["diagnostic"]["exit_code"], -255, 255)
        if value["diagnostic"]["exit_code"] is not None
        else None,
    }
    started, finished, at = result["started_at"], result["finished_at"], result["timestamp"]
    if (
        (started and started > at)
        or (finished and (finished > at or (started and finished < started)))
        or (result["state"] == "planned" and (started or finished))
        or (result["state"] == "started" and (not started or finished))
        or (result["state"] in ("succeeded", "failed") and (not started or not finished))
        or (result["state"] == "cancelled" and not finished)
    ):
        raise AuditError("invalid_action_time")
    return result


def snapshot_record(value):
    """Project CapacitySnapshot v1, deliberately discarding names, URLs and raw payloads."""
    try:
        if value["schema_version"] != 1 or value["kind"] != "CapacitySnapshot":
            raise AuditError("unsupported_snapshot")
        repo = (
            canonical_repo(value["repository"]["nameWithOwner"])
            if value["repository"]["nameWithOwner"] is not None
            else None
        )
        key = canonical_repo(value["repository"]["match_key"]).lower()
        if repo and repo.lower() != key:
            raise AuditError("invalid_repository_identity")
        complete = (
            repo is not None
            and value["sources"]["repository"] == "complete"
            and value["sources"]["queue"] == "complete"
            and value["queue"]["status"] == "complete"
        )
        jobs = value["queue"]["jobs"]
        if not isinstance(jobs, list) or len(jobs) > 10000:
            raise AuditError("payload_too_large")
        projected = []
        for job in jobs if complete else []:
            if job["status"] != "queued":
                raise AuditError("invalid_queue_status")
            row = {
                key: integer(job[key], 1, 2**63 - 1) for key in ("job_id", "run_id", "run_attempt")
            }
            try:
                row["github_created_at"] = timestamp(job.get("created_at"))
            except AuditError:
                row["github_created_at"] = None
            row["required_labels"] = label_list(job.get("required_labels"))
            projected.append(row)
        identities = {(row["run_id"], row["run_attempt"], row["job_id"]) for row in projected}
        if len(identities) != len(projected) or (
            complete
            and (
                type(value["queue"]["queued_job_count"]) is not int
                or value["queue"]["queued_job_count"] != len(projected)
            )
        ):
            raise AuditError("invalid_queue_count")
        return {
            "repository": repo,
            "repo_key": key,
            "observed_at": timestamp(value["observed_at"]),
            "complete": complete,
            "jobs": sorted(
                projected, key=lambda row: (row["run_id"], row["run_attempt"], row["job_id"])
            ),
        }
    except (KeyError, TypeError, AttributeError):
        raise AuditError("invalid_snapshot") from None
