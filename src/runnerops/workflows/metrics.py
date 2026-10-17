"""Read-only bounded job duration metrics from explicitly supplied GitHub job evidence.

The input is a curated WorkflowJobsEvidence document. No GitHub calls and no
untrusted job text is interpreted or sent to a model by this module.
"""
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import statistics

MAX_BYTES = 1024 * 1024
MAX_JOBS = 500


def _seconds(value):
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return d if d.tzinfo else None


def _measure(start, end):
    a, b = _seconds(start), _seconds(end)
    if a is None or b is None:
        return None
    duration = (b - a).total_seconds()
    return round(duration, 3) if 0 <= duration <= 31 * 86400 else None


def read_jobs(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_BYTES:
        raise ValueError("INVALID_JOBS_EVIDENCE_FILE")
    value = json.loads(p.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("kind") != "WorkflowJobsEvidence"
            or value.get("schema_version") != 1 or not isinstance(value.get("jobs"), list)
            or not isinstance(value.get("repository"), str)
            or not 1 <= len(value["jobs"]) <= MAX_JOBS):
        raise ValueError("INVALID_JOBS_EVIDENCE_SCHEMA")
    return value


def summarize(value, repository):
    if value["repository"].casefold() != str(repository).casefold():
        raise ValueError("JOBS_EVIDENCE_REPOSITORY_MISMATCH")
    groups = defaultdict(list)
    unknown = 0
    for job in value["jobs"]:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            unknown += 1
            continue
        name = job["name"]
        if not name or len(name) > 160:
            unknown += 1
            continue
        groups[name].append(job)
    summary = []
    for name, jobs in sorted(groups.items()):
        complete = [j for j in jobs if j.get("status") == "completed"]
        unknown += len(jobs) - len(complete)
        success = [j for j in complete if j.get("conclusion") == "success"]
        queue = [x for j in success if (x := _measure(j.get("created_at"), j.get("started_at"))) is not None]
        runtime = [x for j in success if (x := _measure(j.get("started_at"), j.get("completed_at"))) is not None]
        missing = len(success) - min(len(queue), len(runtime))
        unknown += missing
        def stats(values):
            ordered = sorted(values)
            n = len(ordered)
            return {
                "count": n,
                "median_seconds": round(statistics.median(ordered), 3) if n else None,
                "p95_seconds": ordered[(95 * n + 99) // 100 - 1] if n >= 20 else None,
                "max_seconds": ordered[-1] if n else None,
            }
        summary.append({
            "name": name, "completed": len(complete), "successful": len(success),
            "failed": sum(j.get("conclusion") == "failure" for j in complete),
            "cancelled": sum(j.get("conclusion") == "cancelled" for j in complete),
            "queue": stats(queue), "execution": stats(runtime),
        })
    return {
        "schema_version": 1, "repository": value["repository"],
        "source": "operator_supplied_jobs_evidence",
        "total_jobs": len(value["jobs"]), "unknown_timestamps_or_jobs": unknown,
        "by_job": summary, "status": "inconclusive" if unknown else "complete",
    }
