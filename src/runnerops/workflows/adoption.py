"""Governed, deterministic workflow adoption (no agent-authored YAML rewrites)."""
import difflib
import os
from pathlib import Path
import tempfile

from .discovery import EXTERNAL_EVENTS, SAFE_EVENTS, discover, digest


PROFILES = {
    "hosted": "ubuntu-latest",
    "local-first": "[self-hosted, Linux, X64, local-runner]",
}


def plan(root, profile):
    if profile not in PROFILES:
        raise ValueError("INVALID_ADOPTION_PROFILE")
    snapshot = discover(root)
    changes = []
    blocked = []
    for workflow in snapshot["workflows"]:
        path = workflow["path"]
        if workflow["status"] != "complete":
            blocked.append({"file": path, "code": "AUDIT_INCONCLUSIVE"})
            continue
        events = set(workflow["triggers"])
        if profile == "local-first" and (not events or events - SAFE_EVENTS or events & EXTERNAL_EVENTS):
            blocked.append({"file": path, "code": "UNTRUSTED_OR_UNSUPPORTED_TRIGGERS"})
            continue
        for job in workflow["jobs"]:
            if job["reusable"]:
                continue
            if job["dynamic"] or job["runs_on"] is None:
                blocked.append({"file": path, "job": job["id"], "code": "UNSUPPORTED_RUNS_ON"})
                continue
            if "features" in job:
                if profile == "local-first" and any(x in job["features"] for x in ("container", "services")):
                    blocked.append({"file": path, "job": job["id"], "code": "UNREVIEWED_JOB_ISOLATION"})
                    continue
            if profile == "local-first" and job["runner_kind"] == "hosted" and job["labels"] != ["ubuntu-latest"]:
                blocked.append({"file": path, "job": job["id"], "code": "NONSTANDARD_HOSTED_PLATFORM"})
                continue
            target = PROFILES[profile]
            if job["runs_on"] == target:
                continue
            # Only one physical YAML line with a literal scalar/inline list may change.
            changes.append({"file": path, "job": job["id"], "line": job["line"],
                            "from": job["runs_on"], "to": target, "source_sha256": workflow["sha256"]})
    return {"schema_version": 1, "kind": "WorkflowAdoptionPlan",
            "profile": profile, "root": snapshot["root"],
            "fingerprint": snapshot["fingerprint"],
            "status": "blocked" if blocked else "ready",
            "changes": changes, "blocked": blocked, "mutation": False}


def apply(root, profile, expected_fingerprint, *, confirmed=False):
    """Apply exact planned single-line substitutions with stale-plan protection."""
    if not confirmed or not expected_fingerprint:
        raise ValueError("EXPLICIT_APPROVAL_AND_FINGERPRINT_REQUIRED")
    desired = plan(root, profile)
    if desired["fingerprint"] != expected_fingerprint:
        raise ValueError("STALE_ADOPTION_FINGERPRINT")
    if desired["status"] != "ready":
        raise ValueError("ADOPTION_BLOCKED")
    by_file = {}
    for change in desired["changes"]:
        by_file.setdefault(change["file"], []).append(change)
    staged = []
    directory = Path(desired["root"])
    # Verify every input before the first mutation.
    for name, changes in by_file.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("UNSAFE_WORKFLOW_PATH")
        content = path.read_bytes()
        if digest(content) != changes[0]["source_sha256"]:
            raise ValueError("STALE_WORKFLOW_CONTENT")
        lines = content.decode("utf-8").splitlines(keepends=True)
        for change in changes:
            index = change["line"] - 1
            original = lines[index]
            pre, sep, value = original.partition("runs-on:")
            if not sep or pre != "    " or value.strip() != change["from"]:
                raise ValueError("UNSUPPORTED_ADOPTION_LINE")
            newline = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
            lines[index] = pre + sep + " " + change["to"] + newline
        updated = "".join(lines).encode("utf-8")
        staged.append((path, updated, path.stat().st_mode & 0o777))
    updated_paths = []
    try:
        for path, content, mode in staged:
            fd, temporary = tempfile.mkstemp(prefix=".runnerops-adopt-", dir=str(path.parent))
            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, "wb") as writer:
                    writer.write(content)
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(temporary, path)
                updated_paths.append(path.name)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    except OSError as exc:
        # Explicit partial state. Never claim multi-file atomicity.
        raise RuntimeError("PARTIAL_ADOPTION files=" + ",".join(updated_paths)) from exc
    desired["mutation"] = True
    desired["status"] = "applied"
    desired["applied_files"] = updated_paths
    return desired
