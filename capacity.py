#!/usr/bin/env python3
"""Read-only CapacitySnapshot collector. Public entrypoint: runnerctl capacity."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


CATEGORIES = ("available_now", "busy_capacity", "provisioned_idle", "inconclusive")
RUN_STATUSES = ("queued", "in_progress", "waiting", "pending", "requested")
REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
GITHUB_REMOTE_PREFIXES = (
    "https://github.com/",
    "http://github.com/",
    "ssh://git@github.com/",
    "git@github.com:",
)
COLLECTOR_CACHE_TTL_SECONDS = 60
QUEUE_CACHE_VERSION = 1
MAX_JOB_QUERY_WORKERS = 4
JOB_QUERY_RETRIES = 1
_REPO_CACHE = {}


class EvidenceError(Exception):
    pass


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("command_unavailable") from exc
    if result.returncode:
        # Do not forward raw stderr: it can contain machine or authentication data.
        raise EvidenceError("query_failed")
    return result.stdout.strip()


def collector_metrics():
    return {
        "github_calls": 0,
        "repo_resolution_calls": 0,
        "run_list_calls": 0,
        "job_list_calls": 0,
        "runner_list_calls": 0,
        "job_query_retry_calls": 0,
        "job_query_workers": 0,
        "repo_cache_hits": 0,
        "job_cache_hits": 0,
        "job_cache_misses": 0,
        "job_cache_invalidations": 0,
        "scheduler_interval_seconds": None,
        "scheduler_headroom_ms": None,
        "scheduler_overrun": False,
        "scheduler_near_overrun": False,
        "canonical_source": None,
    }


def record_github_call(metrics, kind):
    if metrics is None:
        return
    metrics["github_calls"] += 1
    if kind:
        metrics[kind] += 1


def reset_process_cache():
    """Test/support hook; collector caches never persist beyond this process."""
    _REPO_CACHE.clear()


def _queue_cache_path(repo):
    root = os.environ.get("RUNNER_CACHE_ROOT")
    if not root:
        root = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    digest = hashlib.sha256(repo_key(repo).encode("utf-8")).hexdigest()[:16]
    return Path(root) / "runnerops" / "capacity" / f"{digest}.json"


def _load_queue_cache(repo):
    try:
        payload = json.loads(_queue_cache_path(repo).read_text(encoding="utf-8"))
        if payload.get("version") != QUEUE_CACHE_VERSION:
            return {}
        return payload.get("runs", {}) if isinstance(payload.get("runs"), dict) else {}
    except (OSError, UnicodeError, ValueError, AttributeError):
        return {}


def _save_queue_cache(repo, runs):
    path = _queue_cache_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            json.dump({"version": QUEUE_CACHE_VERSION, "runs": runs}, handle)
            temporary = Path(handle.name)
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        try:
            temporary.unlink()
        except (UnboundLocalError, OSError):
            pass


def _run_fingerprint(run):
    changed = {
        key: run.get(key)
        for key in ("status", "conclusion", "updated_at", "head_sha", "run_attempt")
    }
    if not changed["updated_at"]:
        return None
    return changed


def _record_scheduler_timing(metrics, wall_time_ms):
    raw_interval = os.environ.get("RUNNER_AUTOSCALE_INTERVAL_SECONDS", "").strip()
    try:
        interval = int(raw_interval)
    except ValueError:
        return
    if interval < 1:
        return
    interval_ms = interval * 1000
    metrics["scheduler_interval_seconds"] = interval
    metrics["scheduler_headroom_ms"] = interval_ms - wall_time_ms
    metrics["scheduler_overrun"] = wall_time_ms >= interval_ms
    metrics["scheduler_near_overrun"] = wall_time_ms >= int(interval_ms * 0.8)


def github_repo_from_remote(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    for prefix in GITHUB_REMOTE_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    else:
        return None
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value if re.fullmatch(REPO_PATTERN, value) else None


def repo_key(value):
    remote = github_repo_from_remote(value)
    if remote is not None:
        return remote.lower()
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def _repo_cache_key(requested):
    return (requested, str(Path.cwd()) if requested == "." else None)


def _cache_repository_identity(cache_key, canonical, now, metrics, source):
    result = (canonical, canonical.lower())
    cached_value = (*result, now + COLLECTOR_CACHE_TTL_SECONDS)
    _REPO_CACHE[cache_key] = cached_value
    _REPO_CACHE[_repo_cache_key(canonical)] = cached_value
    if metrics is not None:
        metrics["canonical_source"] = source
    return result


def resolve_repo(requested, metrics=None):
    cache_key = _repo_cache_key(requested)
    now = time.monotonic()
    cached = _REPO_CACHE.get(cache_key)
    if cached and cached[2] >= now:
        if metrics is not None:
            metrics["repo_cache_hits"] += 1
            metrics["canonical_source"] = "process_cache"
        return cached[0], cached[1]
    if cached:
        _REPO_CACHE.pop(cache_key, None)

    local_origin = None
    if requested == ".":
        try:
            local_origin = command("git", "remote", "get-url", "origin")
        except EvidenceError:
            local_origin = None
        local_canonical = github_repo_from_remote(local_origin)
        if local_canonical is not None:
            return _cache_repository_identity(
                cache_key, local_canonical, now, metrics, "git_remote"
            )
    else:
        trusted = os.environ.get("RUNNEROPS_CANONICAL_REPOSITORY", "").strip()
        if (
            re.fullmatch(REPO_PATTERN, trusted)
            and trusted.casefold() == requested.casefold()
        ):
            return _cache_repository_identity(
                cache_key, trusted, now, metrics, "scheduler"
            )

    target = [] if requested == "." else [requested]
    try:
        record_github_call(metrics, "repo_resolution_calls")
        canonical = command("gh", "repo", "view", *target,
                            "--json", "nameWithOwner", "--jq", ".nameWithOwner")
        if not re.fullmatch(REPO_PATTERN, canonical):
            raise EvidenceError("invalid_response")
        return _cache_repository_identity(
            cache_key, canonical, now, metrics, "remote"
        )
    except EvidenceError:
        if requested == ".":
            key = repo_key(local_origin) if local_origin else None
        else:
            key = repo_key(requested)
        if metrics is not None:
            metrics["canonical_source"] = "fallback"
        return None, key if key and re.fullmatch(REPO_PATTERN, key) else None


def api_pages(
    endpoint,
    field,
    errors,
    source,
    max_pages=100,
    *,
    metrics=None,
    metric_kind=None,
    retries=0,
):
    """Retain partial evidence, but never report a truncated collection as complete."""
    rows = []
    for page in range(1, max_pages + 1):
        separator = "&" if "?" in endpoint else "?"
        failure = None
        for attempt in range(retries + 1):
            try:
                record_github_call(metrics, metric_kind)
                payload = json.loads(command(
                    "gh", "api", "--method", "GET",
                    f"{endpoint}{separator}per_page=100&page={page}"))
                batch, total = payload[field], payload["total_count"]
                if (not isinstance(batch, list) or type(total) is not int or total < 0
                        or any(not isinstance(row, dict) for row in batch)):
                    raise ValueError
                failure = None
                break
            except (EvidenceError, ValueError, KeyError, TypeError) as exc:
                failure = exc
                if attempt < retries:
                    if metrics is not None:
                        metrics["job_query_retry_calls"] += 1
                    continue
        if failure is not None:
            errors.append({"source": source, "reason": "query_failed"})
            return rows
        rows.extend(batch)
        if len(batch) < 100:
            if len(rows) < total:
                errors.append({"source": source, "reason": "incomplete_pagination"})
            return rows
        if len(rows) >= total:
            if max_pages == 10 and total >= 1000:
                errors.append({"source": source, "reason": "pagination_limit"})
            return rows
    errors.append({"source": source, "reason": "pagination_limit"})
    return rows


def labels(value, remote=False):
    if not isinstance(value, list) or not value:
        return None
    if remote:
        value = [item.get("name") if isinstance(item, dict) else None for item in value]
    return value if all(isinstance(item, str) and item for item in value) else None


def positive_id(value):
    return type(value) is int and value > 0


def _collect_run_jobs(repo, run):
    local_errors = []
    local_metrics = collector_metrics()
    endpoint = f"repos/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs"
    rows = api_pages(
        endpoint,
        "jobs",
        local_errors,
        "queue",
        metrics=local_metrics,
        metric_kind="job_list_calls",
        retries=JOB_QUERY_RETRIES,
    )
    return run, rows, local_errors, local_metrics


def collect_queue(repo, now, errors, metrics=None, persist_cache=True):
    queue_error_start = len(errors)
    runs = {}
    for status in RUN_STATUSES:
        for run in api_pages(f"repos/{repo}/actions/runs?status={status}",
                             "workflow_runs", errors, "queue", max_pages=10,
                             metrics=metrics, metric_kind="run_list_calls"):
            if not positive_id(run.get("id")) or not positive_id(run.get("run_attempt")):
                errors.append({"source": "queue", "reason": "invalid_run"})
                continue
            previous = runs.get(run["id"])
            if previous is None or run["run_attempt"] >= previous["run_attempt"]:
                runs[run["id"]] = run

    ordered_runs = sorted(runs.values(), key=lambda run: (run["id"], run["run_attempt"]))
    cached_runs = _load_queue_cache(repo)
    job_batches = []
    runs_to_query = []
    cache_records = {}
    now_epoch = time.time()
    for run in ordered_runs:
        fingerprint = _run_fingerprint(run)
        cache_key = f"{run['id']}:{run['run_attempt']}"
        cached = cached_runs.get(cache_key, {})
        cached_at = cached.get("cached_at", 0) if isinstance(cached, dict) else 0
        if (not errors and fingerprint is not None and isinstance(cached, dict)
                and cached.get("fingerprint") == fingerprint
                and isinstance(cached.get("jobs"), list)
                and now_epoch - cached_at <= COLLECTOR_CACHE_TTL_SECONDS):
            job_batches.append((run, cached["jobs"], [], collector_metrics()))
            if metrics is not None:
                metrics["job_cache_hits"] += 1
        else:
            runs_to_query.append(run)
            if metrics is not None and cached:
                metrics["job_cache_misses"] += 1

    workers = min(MAX_JOB_QUERY_WORKERS, len(runs_to_query))
    if metrics is not None:
        metrics["job_query_workers"] = workers

    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="runnerops-jobs") as executor:
            job_batches.extend(executor.map(
                lambda run: _collect_run_jobs(repo, run), runs_to_query
            ))

    jobs = {}
    for run, rows, batch_errors, batch_metrics in job_batches:
        errors.extend(batch_errors)
        if metrics is not None:
            metrics["github_calls"] += batch_metrics["github_calls"]
            metrics["job_list_calls"] += batch_metrics["job_list_calls"]
            metrics["job_query_retry_calls"] += batch_metrics["job_query_retry_calls"]
        if not batch_errors and _run_fingerprint(run) is not None:
            cache_records[f"{run['id']}:{run['run_attempt']}"] = {
                "fingerprint": _run_fingerprint(run),
                "cached_at": now_epoch,
                "jobs": rows,
            }
        for job in rows:
            if job.get("status") != "queued":
                if job.get("status") not in ("in_progress", "completed", "waiting", "pending"):
                    errors.append({"source": "queue", "reason": "unknown_job_status"})
                continue
            if not positive_id(job.get("id")):
                errors.append({"source": "queue", "reason": "invalid_job"})
                continue
            age = None
            try:
                created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
                if created.tzinfo is None or created > now:
                    raise ValueError
                age = int((now - created).total_seconds())
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
            jobs[job["id"]] = {
                "job_id": job["id"], "name": job.get("name"),
                "workflow_id": run.get("workflow_id"), "workflow_name": run.get("name"),
                "run_id": run["id"], "run_attempt": run["run_attempt"],
                "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
                "run_status": run.get("status"), "status": "queued",
                "created_at": job.get("created_at"), "queue_age_seconds": age,
                "queue_age_source": "job.created_at" if age is not None else None,
                "required_labels": labels(job.get("labels")),
            }
    if persist_cache and len(errors) == queue_error_start and cache_records:
        _save_queue_cache(repo, cache_records)
    elif metrics is not None and persist_cache and cache_records:
        metrics["job_cache_invalidations"] += 1
    return sorted(jobs.values(), key=lambda j: (-(j["queue_age_seconds"] or 0), j["job_id"]))


def read_registry(errors):
    records = []
    try:
        lines = Path(os.environ["RUNNERS_CONFIG"]).read_text().splitlines()
    except (OSError, UnicodeError):
        errors.append({"source": "local", "reason": "registry_unavailable"})
        return records
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [part.strip() for part in line.split("|")]
        if (len(fields) < 5 or not fields[0] or not Path(fields[1]).is_absolute()
                or fields[4].lower() not in ("true", "false")
                or not re.fullmatch(REPO_PATTERN, repo_key(fields[3]))):
            errors.append({"source": "local", "reason": "invalid_registry_record"})
            continue
        name, path, _, repo, enabled = fields[:5]
        records.append({"name": name, "path": path, "repo": repo_key(repo),
                        "enabled": enabled.lower() == "true"})
    if (len({r["name"] for r in records}) != len(records)
            or len({r["path"] for r in records}) != len(records)):
        errors.append({"source": "local", "reason": "duplicate_registry_record"})
    return records


def observe_service(path):
    empty = {"unit": None, "state": "unknown", "boot": None, "reason": "systemd_unknown"}
    properties = "LoadState,ActiveState,SubState,UnitFileState,WorkingDirectory,Result"

    def show(unit):
        output = command("systemctl", "show", unit, f"--property={properties}", "--no-pager")
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    try:
        try:
            unit = (path / ".service").read_text().strip()
        except FileNotFoundError:
            unit = ""
        if not unit:
            candidates = command("systemctl", "list-unit-files", "actions.runner.*.service",
                                 "--no-legend", "--no-pager")
            matches = []
            for line in candidates.splitlines():
                candidate = line.split()[0]
                if show(candidate).get("WorkingDirectory") == str(path):
                    matches.append(candidate)
            if len(matches) != 1:
                return empty
            unit = matches[0]
        if not re.fullmatch(r"actions\.runner\.[A-Za-z0-9_.@\\:-]+\.service", unit):
            return empty
        evidence = show(unit)
        if evidence.get("LoadState") != "loaded" or evidence.get("WorkingDirectory") != str(path):
            return empty
        state = evidence.get("ActiveState", "unknown")
        boot = evidence.get("UnitFileState")
        result = {"unit": unit, "state": "unknown", "boot": boot, "reason": "systemd_unknown"}
        if state == "active" and evidence.get("SubState") == "running":
            result.update(state="active", reason="systemd_active")
        elif state == "failed":
            result.update(state="failed", reason="systemd_failed")
        elif (state == "inactive" and evidence.get("SubState") == "dead"
              and evidence.get("Result") == "success" and boot == "disabled"
              and os.environ.get("RUNNER_BOOT_POLICY", "on-demand") == "on-demand"):
            result.update(state="healthy_idle", reason="on_demand_inactive")
        return result
    except (EvidenceError, OSError, UnicodeError):
        return empty


def collect_local(records):
    for record in records:
        path = Path(record["path"])
        record["local"] = observe_service(path)
        try:
            registration = json.loads((path / ".runner").read_text(encoding="utf-8-sig"))
            if (not isinstance(registration, dict) or not positive_id(registration.get("agentId"))
                    or not isinstance(registration.get("agentName"), str)
                    or not registration["agentName"] or not os.access(path / "run.sh", os.X_OK)):
                raise ValueError
            if registration.get("gitHubUrl") and repo_key(registration["gitHubUrl"]) != record["repo"]:
                raise ValueError
            record["registration"] = {"id": registration["agentId"], "name": registration["agentName"]}
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            record["registration"] = None


def correlate(records, remote, remote_complete):
    runners, used = [], set()
    ids = Counter(r["registration"]["id"] for r in records if r["registration"])
    for record in records:
        registration = record["registration"]
        matches = [r for r in remote if registration and r.get("id") == registration["id"]]
        github = matches[0] if len(matches) == 1 else None
        if github:
            used.add(github["id"])
        reason, category = record["local"]["reason"], "inconclusive"
        if not record["enabled"]:
            reason = "registry_disabled"
        elif not registration:
            reason = "invalid_registration"
        elif ids[registration["id"]] > 1:
            reason = "duplicate_registration"
        elif not remote_complete:
            reason = "github_runners_unknown"
        elif not github:
            reason = "registration_not_found"
        elif registration["name"] != github.get("name"):
            reason = "registration_identity_mismatch"
        elif labels(github.get("labels"), remote=True) is None:
            reason = "github_labels_unknown"
        elif github.get("status") not in ("online", "offline") or type(github.get("busy")) is not bool:
            reason = "github_state_unknown"
        elif record["local"]["state"] == "active" and github["status"] == "online":
            category = "busy_capacity" if github["busy"] else "available_now"
            reason = "active_online_busy" if github["busy"] else "active_online_idle"
        elif (record["local"]["state"] == "healthy_idle"
              and github["status"] == "offline" and not github["busy"]):
            category, reason = "provisioned_idle", "on_demand_inactive"
        elif record["local"]["state"] != "unknown":
            reason = "systemd_failed" if record["local"]["state"] == "failed" else "state_disagreement"
        runners.append({"name": record["name"], "registration_id": registration["id"] if registration else None,
                        "scope": "local", "enabled": record["enabled"], "local": record["local"],
                        "github": remote_evidence(github), "category": category, "reason": reason})
    for github in remote:
        if github.get("id") not in used:
            runners.append({"name": github.get("name"), "registration_id": github.get("id"),
                            "scope": "remote_only", "enabled": None, "local": None,
                            "github": remote_evidence(github), "category": "inconclusive",
                            "reason": "local_evidence_missing"})
    return runners


def remote_evidence(github):
    if github is None:
        return None
    return {"id": github.get("id"), "name": github.get("name"), "status": github.get("status"),
            "busy": github.get("busy"), "labels": labels(github.get("labels"), remote=True)}


def match_jobs(jobs, runners, complete):
    for job in jobs:
        required = job["required_labels"]
        matched, unknown = [], not complete or not required
        for runner in runners:
            if runner["enabled"] is False:
                continue
            available_labels = (runner["github"] or {}).get("labels")
            if not available_labels:
                unknown = True
            elif required and {label.lower() for label in required} <= {label.lower() for label in available_labels}:
                matched.append(runner)
        counts = {category: sum(r["category"] == category for r in matched) for category in CATEGORIES}
        if not required:
            status = "inconclusive"
        elif counts["available_now"]:
            status = "available_now"
        elif unknown or counts["inconclusive"]:
            status = "inconclusive"
        elif counts["busy_capacity"]:
            status = "busy_capacity"
        elif counts["provisioned_idle"]:
            status = "provisioned_idle"
        else:
            status = "no_matching_capacity"
        job.update(capacity_status=status, matching_capacity=counts,
                   matching_runner_ids=[r["registration_id"] for r in matched],
                   matching_local_runner_names=[r["name"] for r in matched if r["scope"] == "local"])


def snapshot(requested, persist_cache=True):
    started = time.monotonic()
    metrics = collector_metrics()
    now = datetime.now(timezone.utc)
    errors = []
    canonical, key = resolve_repo(requested, metrics=metrics)
    if canonical is None:
        errors.append({"source": "repository", "reason": "canonical_identity_unavailable"})
    records = read_registry(errors)
    collect_local(records)
    selected = [record for record in records if key and record["repo"] == key]
    jobs, remote = [], []
    if canonical:
        jobs = collect_queue(
            canonical, now, errors, metrics=metrics, persist_cache=persist_cache
        )
        remote = api_pages(f"repos/{canonical}/actions/runners", "runners", errors, "github_runners",
                           metrics=metrics, metric_kind="runner_list_calls")
        valid_remote = [r for r in remote if positive_id(r.get("id")) and isinstance(r.get("name"), str)]
        if len(valid_remote) != len(remote) or len({r["id"] for r in valid_remote}) != len(valid_remote):
            errors.append({"source": "github_runners", "reason": "invalid_runners"})
        remote = valid_remote
    sources = {source: "inconclusive" if any(e["source"] == source for e in errors) else "complete"
               for source in ("repository", "queue", "local", "github_runners")}
    if not canonical:
        sources["queue"] = sources["github_runners"] = "inconclusive"
    runners = correlate(selected, remote, sources["github_runners"] == "complete")
    match_jobs(jobs, runners, all(sources[s] == "complete" for s in ("local", "github_runners")))
    counts = {category: sum(r["category"] == category for r in runners) for category in CATEGORIES}
    active_count = len({record["local"]["unit"] for record in records if record["local"]["state"] == "active"})
    host_complete = sources["local"] == "complete" and all(r["local"]["state"] != "unknown" for r in records)
    matching = [j for j in jobs if j["matching_local_runner_names"]]

    def oldest(items):
        # Unknown ages cannot safely compete for "oldest".
        if not items or any(j["queue_age_seconds"] is None for j in items):
            return None
        return max(items, key=lambda j: j["queue_age_seconds"])["job_id"]

    inconclusive = (any(s != "complete" for s in sources.values()) or counts["inconclusive"] > 0
                    or not host_complete or any(j["capacity_status"] == "inconclusive"
                                               or j["queue_age_seconds"] is None for j in jobs))
    metrics["wall_time_ms"] = max(0, int(round((time.monotonic() - started) * 1000)))
    _record_scheduler_timing(metrics, metrics["wall_time_ms"])
    metrics["cache_scope"] = "persistent_queue_and_process_identity"
    metrics["cache_ttl_seconds"] = COLLECTOR_CACHE_TTL_SECONDS
    return {
        "schema_version": 1, "kind": "CapacitySnapshot", "observed_at": now.isoformat(),
        "status": "inconclusive" if inconclusive else "complete",
        "repository": {"requested": requested, "nameWithOwner": canonical, "match_key": key},
        "sources": sources, "errors": errors,
        "queue": {"status": sources["queue"], "observed_queued_job_count": len(jobs),
                  "queued_job_count": len(jobs) if sources["queue"] == "complete" else None,
                  "oldest_queued_job_id": oldest(jobs) if sources["queue"] == "complete" else None,
                  "oldest_matching_queued_job_id": oldest(matching) if sources["queue"] == "complete" else None,
                  "jobs": jobs},
        "capacity": {"counts": counts, "runners": runners, "matching_basis": "required_labels",
                     "remote_scope": "repository_runners_endpoint"},
        "host": {"active_local_runner_count": active_count if host_complete else None,
                 "observed_active_local_runner_count": active_count,
                 "status": "complete" if host_complete else "inconclusive"},
        "collector": metrics,
    }


def render(snapshot):
    repo = snapshot["repository"]
    print(f"Capacity: {repo['nameWithOwner'] or repo['match_key'] or '?'} ({snapshot['status']})")
    queue = snapshot["queue"]
    count = queue["queued_job_count"]
    print(f"Queue: {count if count is not None else 'unknown'} queued jobs; observed={queue['observed_queued_job_count']}")
    print("Capacity: " + ", ".join(f"{key.replace('_', ' ')}={value}" for key, value in snapshot["capacity"]["counts"].items()))
    print(f"Host active local runners: {snapshot['host']['active_local_runner_count']}")
    collector = snapshot.get("collector", {})
    print(
        "Collector: "
        f"wall={collector.get('wall_time_ms')}ms github_calls={collector.get('github_calls')} "
        f"runs={collector.get('run_list_calls')} jobs={collector.get('job_list_calls')} "
        f"runners={collector.get('runner_list_calls')} repo_cache_hits={collector.get('repo_cache_hits')}"
    )
    for runner in snapshot["capacity"]["runners"]:
        github = runner["github"] or {}
        print(f"  {runner['name']}: {runner['category'].replace('_', ' ')}; reason={runner['reason']}"
              f"; local={(runner['local'] or {}).get('state', 'unknown')}; GitHub={github.get('status', 'unknown')}"
              f"; busy={github.get('busy')}")
    for job in queue["jobs"]:
        print(f"  Job {job['job_id']} {job['name']} (run={job['run_id']} attempt={job['run_attempt']}):"
              f" age={job['queue_age_seconds']}s labels={json.dumps(job['required_labels'])}"
              f" capacity={job['capacity_status']}")
    for error in snapshot["errors"]:
        print(f"  Evidence: {error['source']}={error['reason']}")
    print("Matching uses labels; scheduling eligibility and assignment are not verified.")


def main():
    parser = argparse.ArgumentParser(prog="runnerctl capacity", description=__doc__)
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.repository != "." and not re.fullmatch(REPO_PATTERN, args.repository):
        parser.error("expected owner/repo or .")
    result = snapshot(args.repository)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        render(result)
    return 0 if result["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
