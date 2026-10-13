"""Workflow-focused bounded interpretation of OperationalEvidence v1."""
import json
from pathlib import Path

from runnerops.operational.report import build_report, duration

MAX_EVIDENCE_BYTES = 256 * 1024


def read_evidence(path):
    if not isinstance(path, (str, Path)):
        raise ValueError("INVALID_EVIDENCE_PATH")
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("INVALID_EVIDENCE_FILE")
    value = json.loads(p.read_text(encoding="utf-8"))
    return validate(value)


def validate(evidence):
    if not isinstance(evidence, dict) or evidence.get("kind") != "OperationalEvidence" or evidence.get("schema_version") != 1:
        raise ValueError("UNSUPPORTED_OPERATIONAL_EVIDENCE")
    if not isinstance(evidence.get("ci"), dict) or not isinstance(evidence.get("autoscale"), dict):
        raise ValueError("INCOMPLETE_OPERATIONAL_EVIDENCE")
    if not isinstance(evidence.get("incomplete_evidence"), list):
        raise ValueError("MISSING_EVIDENCE_LIMITATIONS")
    return evidence


def analyze(evidence, jobs_evidence=None):
    value = validate(evidence)
    ci = value["ci"]
    autoscale = value["autoscale"]
    gaps = sorted(set(
        gap.get("reason") for gap in value["incomplete_evidence"]
        if isinstance(gap, dict) and isinstance(gap.get("reason"), str)
    ))
    decisions = autoscale.get("decisions") or {}
    actions = autoscale.get("actions") or {}
    counts = (actions.get("by_state") or {})
    failures = sum(counts.get(k, 0) for k in ("failed", "FAILED") if isinstance(counts.get(k, 0), int))
    signals = []
    queue = ci.get("queued_jobs_observed")
    if isinstance(queue, int) and queue > 0:
        signals.append({"code": "QUEUED_WORK_OBSERVED", "class": "OBSERVED",
                        "value": queue, "scope": "current_snapshot_only"})
    if failures:
        signals.append({"code": "FAILED_AUTOSCALE_ACTIONS_RECORDED", "class": "OBSERVED",
                        "value": failures, "scope": "available_bounded_history"})
    if gaps:
        signals.append({"code": "EVIDENCE_GAPS", "class": "UNKNOWN",
                        "reasons": gaps, "scope": "do_not_infer_unavailable_history"})
    jobs_summary = None
    if jobs_evidence is not None:
        from .metrics import summarize
        jobs_summary = summarize(jobs_evidence, value.get("repository", {}).get("nameWithOwner"))
        signals.append({"code": "JOB_TIMINGS_FROM_EXPLICIT_EVIDENCE", "class": "OBSERVED",
                        "count": jobs_summary["total_jobs"], "scope": "bounded_supplied_sample"})
        if jobs_summary["status"] != "complete":
            gaps.append("job_timestamps_incomplete")
    return {
        "schema_version": 1, "kind": "WorkflowIntelligence",
        "repository": value.get("repository", {}).get("nameWithOwner"),
        "period": value.get("period"),
        "ci": {"queued_jobs_observed": queue, "queue_age_seconds": ci.get("queue_age_seconds")},
        "jobs": jobs_summary,
        "autoscale": {"decisions_by_kind": decisions.get("by_kind", {}),
                      "actions_by_kind": actions.get("by_kind", {}),
                      "actions_by_state": counts},
        "signals": signals, "incomplete_evidence": gaps,
        "status": "inconclusive" if gaps else "complete",
        "mutation": False, "model_used": False,
    }


def live(repository=".", since="24h"):
    seconds = duration(since)
    return build_report(repository, seconds)
