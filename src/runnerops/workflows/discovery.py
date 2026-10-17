"""Offline, read-only and conservative GitHub Actions workflow discovery.

This is a bounded structural extractor, NOT a complete YAML parser. Unsupported
representations remain unknown; downstream adoption refuses to modify them.
"""
import hashlib
import json
from pathlib import Path
import re

SCHEMA_VERSION = 1
MAX_WORKFLOWS = 100
MAX_BYTES = 256 * 1024
KEY = re.compile(r"^( *)([A-Za-z0-9_-]+):(?:[ \t]*(.*))?$")
HOSTED = re.compile(r"^(ubuntu|windows|macos)-[A-Za-z0-9_.-]+$", re.I)
EXTERNAL_EVENTS = frozenset({
    "pull_request", "pull_request_target", "workflow_run", "issue_comment",
    "repository_dispatch", "workflow_call", "discussion", "discussion_comment",
    "issues", "issue_comment", "public", "fork", "watch",
})
SAFE_EVENTS = frozenset({"push", "workflow_dispatch", "schedule", "release"})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _item(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _sequence(value):
    value = value.strip()
    if "$" + "{{" in value or "!" in value or "&" in value or "*" in value:
        return None
    if value.startswith("[") and value.endswith("]"):
        content = value[1:-1].strip()
        raw = [x.strip() for x in content.split(",")] if content else []
    elif value.startswith("[") or value.endswith("]") or not value:
        return None
    else:
        raw = [value]
    result = [_item(x) for x in raw]
    if not result or any(not x or not re.fullmatch(r"[A-Za-z0-9_.:/+-]+", x) for x in result):
        return None
    return result


def _diagnostic(code, path, job=None, level="warning"):
    return {"code": code, "file": path, "job": job, "severity": level}


def _read_one(path, relative):
    out = {"path": relative, "sha256": None, "triggers": [], "jobs": [],
           "diagnostics": [], "status": "complete"}
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("SYMLINK_OR_NOT_FILE")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("FILE_TOO_LARGE")
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise ValueError("FILE_TOO_LARGE")
        source = data.decode("utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        out["diagnostics"].append(_diagnostic(
            str(exc) if isinstance(exc, ValueError) else "UNREADABLE_FILE", relative, level="error"))
        out["status"] = "inconclusive"
        return out

    out["sha256"] = digest(data)
    lines = source.splitlines(keepends=True)
    scope = None
    job = None
    on_indent = None
    events = []
    saw_on = False
    saw_jobs = False
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if "\t" in line[:len(line) - len(line.lstrip())]:
            out["diagnostics"].append(_diagnostic("TABS_IN_INDENT", relative, level="error"))
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = KEY.match(line)
        if not match:
            continue
        spaces, key, value = match.groups()
        indent = len(spaces)
        value = (value or "").strip()
        if indent == 0:
            job = None
            scope = key
            if key == "on":
                saw_on = True
                if value:
                    seq = _sequence(value)
                    if seq is None:
                        out["diagnostics"].append(_diagnostic("UNSUPPORTED_TRIGGERS", relative, level="error"))
                    else:
                        events.extend(seq)
                on_indent = 2
            if key == "jobs":
                saw_jobs = True
            continue
        if scope == "on" and indent == on_indent:
            events.append(key)
        if scope == "jobs":
            if indent == 2:
                job = {"id": key, "runner_kind": "unknown", "labels": [],
                       "runs_on": None, "line": None, "needs": [], "reusable": False,
                       "dynamic": False}
                out["jobs"].append(job)
            elif indent == 4 and job is not None:
                if key == "runs-on":
                    job["line"] = number
                    job["runs_on"] = value
                    labels = _sequence(value)
                    if labels is None:
                        job["dynamic"] = True
                        out["diagnostics"].append(_diagnostic("DYNAMIC_OR_UNSUPPORTED_RUNS_ON", relative, job["id"], "error"))
                    elif any(x.casefold() == "self-hosted" for x in labels):
                        job["runner_kind"] = "self-hosted"
                        job["labels"] = labels
                    elif len(labels) == 1 and HOSTED.fullmatch(labels[0]):
                        job["runner_kind"] = "hosted"
                        job["labels"] = labels
                    else:
                        job["dynamic"] = True
                        out["diagnostics"].append(_diagnostic("CUSTOM_OR_UNSUPPORTED_RUNS_ON", relative, job["id"], "error"))
                if key == "needs":
                    needed = _sequence(value)
                    if needed is None:
                        out["diagnostics"].append(_diagnostic("DYNAMIC_NEEDS", relative, job["id"], "error"))
                    else:
                        job["needs"] = needed
                if key == "uses":
                    job["reusable"] = True
                if key in ("container", "services", "environment", "permissions", "strategy"):
                    # Metadata retained without making a security assertion about values.
                    job.setdefault("features", []).append(key)

    out["triggers"] = sorted(set(events))
    if not saw_on or not events:
        out["diagnostics"].append(_diagnostic("TRIGGERS_UNKNOWN", relative, level="error"))
    if not saw_jobs or not out["jobs"]:
        out["diagnostics"].append(_diagnostic("JOBS_UNKNOWN", relative, level="error"))
    for job in out["jobs"]:
        if job["runs_on"] is None and not job["reusable"]:
            out["diagnostics"].append(_diagnostic("MISSING_RUNS_ON", relative, job["id"], "error"))
        if job["runner_kind"] == "self-hosted" and EXTERNAL_EVENTS.intersection(events):
            out["diagnostics"].append(_diagnostic("UNTRUSTED_EVENT_SELF_HOSTED", relative, job["id"], "error"))
    if any(x["severity"] == "error" for x in out["diagnostics"]):
        out["status"] = "inconclusive"
    return out


def discover(root):
    """Inspect a checkout or its workflow directory; never follow workflow symlinks."""
    root = Path(root).absolute()
    if root.is_file():
        directory, files = root.parent, [root]
    else:
        directory = root / ".github" / "workflows"
        if root.name == "workflows":
            directory = root
        files = sorted(
            [p for p in directory.iterdir() if p.suffix.lower() in (".yml", ".yaml")],
            key=lambda p: p.name,
        ) if directory.is_dir() and not directory.is_symlink() else []
    if len(files) > MAX_WORKFLOWS:
        raise ValueError("WORKFLOW_LIMIT_EXCEEDED")
    if not files:
        return {"schema_version": SCHEMA_VERSION, "kind": "WorkflowDiscovery",
                "root": str(root), "fingerprint": digest(b"[]"), "status": "inconclusive",
                "workflows": [], "diagnostics": [_diagnostic("NO_WORKFLOWS", str(directory), level="error")]}
    workflows = [_read_one(path, path.name) for path in files]
    evidence = [{"path": w["path"], "sha256": w["sha256"]} for w in workflows]
    return {"schema_version": SCHEMA_VERSION, "kind": "WorkflowDiscovery",
            "root": str(directory), "fingerprint": digest(canonical(evidence).encode()),
            "status": ("complete" if all(x["status"] == "complete" for x in workflows) else "inconclusive"),
            "workflows": workflows,
            "diagnostics": [item for w in workflows for item in w["diagnostics"]]}


def exit_code(payload):
    return 0 if payload["status"] == "complete" else 3
