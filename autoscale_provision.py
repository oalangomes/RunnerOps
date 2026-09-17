"""Bounded local provisioning helpers for governed PROVISION_LOCAL actions.

This module deliberately reuses the public ``runnerctl add`` boundary. It owns
policy normalization, deterministic pool slots, template compatibility and
machine-side classification of add outcomes; it never handles registration
tokens itself.
"""

import os
import re
import subprocess
from pathlib import Path

from autoscale_contracts import AuditError, canonical_repo, label_list

PROFILES = {"generic", "node", "python", "flutter", "java", "go", "dotnet"}
ARCHES = {"auto", "x64", "arm64"}
PREFIX_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}")
GROUP_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
VERSION_PATTERN = re.compile(r"(?:latest|[0-9]+(?:\.[0-9]+){1,3})")


class ProvisionPolicyError(Exception):
    def __init__(self, code="invalid_local_provision_policy"):
        self.code = code
        super().__init__(code)


def _boolean(name, default=False, env=None):
    env = os.environ if env is None else env
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ProvisionPolicyError()


def _integer(name, default, minimum=0, maximum=10000, env=None):
    env = os.environ if env is None else env
    raw = str(env.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ProvisionPolicyError() from None
    if not minimum <= value <= maximum:
        raise ProvisionPolicyError()
    return value


def _bounded(name, pattern, default="", env=None):
    env = os.environ if env is None else env
    value = str(env.get(name, default)).strip()
    if value and not pattern.fullmatch(value):
        raise ProvisionPolicyError()
    return value


def _labels(env=None):
    env = os.environ if env is None else env
    raw = str(env.get("RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS", "")).strip()
    if not raw:
        return []
    values = [value.strip() for value in raw.split(",")]
    if any(not value for value in values):
        raise ProvisionPolicyError()
    try:
        return label_list(values)
    except AuditError:
        raise ProvisionPolicyError() from None


def load_provision_policy(env=None):
    """Return the normalized policy fragment that must join the planner fingerprint."""

    env = os.environ if env is None else env
    enabled = _boolean("RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED", False, env)
    maximum = _integer("RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS", 0, 0, 10000, env)
    profile = _bounded(
        "RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE",
        re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}"),
        env=env,
    ).lower()
    group = _bounded("RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP", GROUP_PATTERN, env=env)
    prefix = _bounded("RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX", PREFIX_PATTERN, env=env)
    version = _bounded(
        "RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_VERSION",
        VERSION_PATTERN,
        default="latest",
        env=env,
    )
    arch = _bounded(
        "RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_ARCH",
        re.compile(r"(?:auto|x64|arm64)"),
        default="auto",
        env=env,
    ).lower()
    labels = _labels(env)

    if profile and profile not in PROFILES:
        raise ProvisionPolicyError()
    if arch not in ARCHES:
        raise ProvisionPolicyError()
    if enabled and (maximum <= 0 or not profile or not group or not prefix or not labels):
        raise ProvisionPolicyError()

    return {
        "enabled": enabled,
        "max_local_runners": maximum,
        "template": {
            "profile": profile or None,
            "group": group or None,
            "labels": labels,
            "name_prefix": prefix or None,
            "runner_version": version,
            "runner_arch": arch,
        },
    }


def _local_rows(snapshot):
    sources = snapshot.get("sources", {})
    rows = snapshot.get("capacity", {}).get("runners")
    if sources.get("local") != "complete" or not isinstance(rows, list):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    return [row for row in rows if row.get("scope") == "local"]


def local_pool_size(snapshot):
    rows = _local_rows(snapshot)
    return None if rows is None else len(rows)


def select_pool_slot(snapshot, prefix, maximum):
    """Choose the lowest free deterministic slot while counting every local record."""

    rows = _local_rows(snapshot)
    if rows is None or not PREFIX_PATTERN.fullmatch(prefix) or type(maximum) is not int or maximum <= 0:
        return None
    if len(rows) >= maximum:
        return None
    names = {
        row.get("name", "").casefold()
        for row in rows
        if isinstance(row.get("name"), str) and row.get("name")
    }
    width = max(2, len(str(maximum)))
    for slot in range(1, maximum + 1):
        candidate = f"{prefix}-{slot:0{width}d}"
        if candidate.casefold() not in names:
            return candidate
    return None


def compatible_scopes(qualified_scopes, template_labels):
    """Return exact qualified scopes that the explicit template can satisfy."""

    if not isinstance(qualified_scopes, list) or not isinstance(template_labels, list):
        return []
    offered = {label.casefold() for label in template_labels if isinstance(label, str) and label}
    result = []
    for scope in qualified_scopes:
        if not isinstance(scope, list) or not scope or any(not isinstance(label, str) or not label for label in scope):
            continue
        required = {label.casefold() for label in scope}
        if required <= offered:
            result.append(sorted(set(scope), key=str.casefold))
    return sorted(result, key=lambda values: [value.casefold() for value in values])


def provisioning_candidate(snapshot, policy, qualified_scopes):
    """Return a deterministic target/evidence record or a stable non-action reason."""

    if not isinstance(policy, dict) or not isinstance(policy.get("template"), dict):
        return {"status": "inconclusive", "reason": "LOCAL_PROVISION_POLICY_INVALID"}
    if not policy.get("enabled"):
        return {"status": "blocked", "reason": "LOCAL_PROVISION_DISABLED"}
    pool_size = local_pool_size(snapshot)
    if pool_size is None:
        return {"status": "inconclusive", "reason": "LOCAL_POOL_EVIDENCE_INCONCLUSIVE"}
    maximum = policy.get("max_local_runners")
    if type(maximum) is not int or maximum <= 0:
        return {"status": "inconclusive", "reason": "LOCAL_PROVISION_POLICY_INVALID"}
    if pool_size >= maximum:
        return {
            "status": "blocked",
            "reason": "LOCAL_POOL_AT_MAX",
            "current_local_pool_size": pool_size,
            "max_local_pool_size": maximum,
        }

    template = policy["template"]
    scopes = compatible_scopes(qualified_scopes, template.get("labels", []))
    if not scopes:
        return {
            "status": "blocked",
            "reason": "LOCAL_PROVISION_TEMPLATE_INCOMPATIBLE",
            "current_local_pool_size": pool_size,
            "max_local_pool_size": maximum,
        }
    target = select_pool_slot(snapshot, template.get("name_prefix"), maximum)
    if target is None:
        return {"status": "inconclusive", "reason": "LOCAL_POOL_SLOT_INCONCLUSIVE"}
    return {
        "status": "candidate",
        "reason": "LOCAL_POOL_BELOW_MAX",
        "target": target,
        "selected_scope": scopes[0],
        "current_local_pool_size": pool_size,
        "max_local_pool_size": maximum,
        "template_labels": template["labels"],
    }


def _command(repository, target, policy, *, plan=False, runnerctl=None):
    repository = canonical_repo(repository)
    template = policy["template"]
    executable = Path(runnerctl) if runnerctl is not None else Path(__file__).resolve().parent / "runnerctl"
    command = [
        str(executable),
        "add",
        repository,
        "--profile",
        template["profile"],
        "--group",
        template["group"],
        "--labels",
        ",".join(template["labels"]),
        "--name",
        target,
        "--runner-version",
        template["runner_version"],
        "--runner-arch",
        template["runner_arch"],
    ]
    if plan:
        command.append("--plan")
    return command


def _run(command, timeout):
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _planned_name(output):
    for line in output.splitlines():
        if line.startswith("- runner name: "):
            return line.split(": ", 1)[1].strip()
    return None


def _applied_name(output):
    for line in output.splitlines():
        if line.startswith("[OK] runner="):
            value = line[len("[OK] runner="):].split(" ", 1)[0].strip()
            return value or None
    return None


def provision_exact(repository, target, policy, *, runnerctl=None, timeout=180):
    """Preview then apply one exact target without exposing command output to callers.

    ``runnerctl add --plan`` is intentionally executed first. If its effective
    collision-resolved name differs from the immutable action target, the function
    aborts before the registration-token boundary. The apply result is classified
    only into bounded codes; raw output/tokens are never returned.
    """

    if not isinstance(target, str) or not PREFIX_PATTERN.fullmatch(target.rsplit("-", 1)[0]):
        return {"status": "failed", "code": "PROVISION_TARGET_INVALID", "exit_code": 2}

    preview = _run(_command(repository, target, policy, plan=True, runnerctl=runnerctl), timeout)
    if preview is None:
        return {"status": "failed", "code": "PROVISION_PREVIEW_UNAVAILABLE", "exit_code": 1}
    preview_output = preview.stdout + preview.stderr
    if preview.returncode != 0:
        return {"status": "failed", "code": "PROVISION_PREVIEW_FAILED", "exit_code": preview.returncode}
    if _planned_name(preview_output) != target:
        return {"status": "failed", "code": "PROVISION_TARGET_COLLISION", "exit_code": 1}

    applied = _run(_command(repository, target, policy, plan=False, runnerctl=runnerctl), timeout)
    if applied is None:
        return {"status": "inconclusive", "code": "PROVISION_EXECUTION_UNKNOWN", "exit_code": 1}
    output = applied.stdout + applied.stderr
    if applied.returncode == 0:
        if _applied_name(output) != target:
            return {"status": "inconclusive", "code": "PROVISION_TARGET_MISMATCH", "exit_code": 1}
        return {"status": "ok", "code": "PROVISION_ADD_COMPLETED", "exit_code": 0}
    if "[PARTIAL]" in output:
        return {"status": "inconclusive", "code": "PROVISION_PARTIAL", "exit_code": applied.returncode}
    if "[INCONCLUSIVE]" in output:
        return {"status": "inconclusive", "code": "PROVISION_OUTCOME_UNKNOWN", "exit_code": applied.returncode}
    return {"status": "failed", "code": "PROVISION_ADD_FAILED", "exit_code": applied.returncode}


def provisioned_identity(snapshot, target):
    """Return positive registration id only for the exact healthy on-demand target."""

    rows = _local_rows(snapshot)
    if rows is None:
        return None
    matches = [row for row in rows if row.get("name") == target]
    if len(matches) != 1:
        return None
    row = matches[0]
    local = row.get("local") or {}
    github = row.get("github") or {}
    registration_id = row.get("registration_id")
    if (
        row.get("enabled") is True
        and row.get("category") == "provisioned_idle"
        and local.get("state") == "healthy_idle"
        and github.get("status") == "offline"
        and github.get("busy") is False
        and type(registration_id) is int
        and registration_id > 0
        and github.get("id") == registration_id
    ):
        return str(registration_id)
    return None
