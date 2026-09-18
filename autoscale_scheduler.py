#!/usr/bin/env python3
"""User-systemd scheduler for the governed one-shot autoscale controller.

This module owns unit installation and inspection only.  Every scheduled
execution crosses the existing ``runnerctl autoscale run-once`` boundary; it
does not collect controller evidence, plan capacity, or mutate runners itself.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import capacity


REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
DEFAULT_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 86400


class SchedulerError(Exception):
    def __init__(self, code, exit_code=2):
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


def _integer_env(name, default, minimum, maximum):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise SchedulerError("INVALID_SCHEDULER_INTERVAL") from None
    if not minimum <= value <= maximum:
        raise SchedulerError("INVALID_SCHEDULER_INTERVAL")
    return value


def interval_seconds():
    interval = _integer_env(
        "RUNNER_AUTOSCALE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS, 1, MAX_INTERVAL_SECONDS
    )
    gap = _integer_env("RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS", 300, 1, 86400)
    if interval >= gap:
        raise SchedulerError("INTERVAL_MUST_BE_BELOW_QUEUE_GAP")
    return interval


def canonical_repository(requested):
    """Resolve nameWithOwner before naming a persistent scheduler unit."""
    target = [] if requested == "." else [requested]
    try:
        canonical = capacity.command(
            "gh", "repo", "view", *target, "--json", "nameWithOwner", "--jq", ".nameWithOwner"
        )
    except capacity.EvidenceError:
        raise SchedulerError("CANONICAL_REPOSITORY_UNAVAILABLE", 3) from None
    if not re.fullmatch(REPO_PATTERN, canonical):
        raise SchedulerError("CANONICAL_REPOSITORY_UNAVAILABLE", 3)
    return canonical


def existing_scheduler_repository(requested):
    """Find an existing scheduler even when GitHub authentication is unavailable.

    Unit identities are case-insensitive hashes of the repository slug, so the
    normalized local key is the same identity that a prior canonical enable used.
    This keeps ``disable owner/repo`` available as an operational safety action.
    """
    try:
        return canonical_repository(requested)
    except SchedulerError:
        _, key = capacity.resolve_repo(requested)
        if key and re.fullmatch(REPO_PATTERN, key):
            return key
        raise SchedulerError("CANONICAL_REPOSITORY_UNAVAILABLE", 3) from None


def _xdg_config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def _state_root():
    root = os.environ.get("RUNNER_STATE_ROOT")
    if root:
        return Path(root)
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "actions-runners"


def unit_identity(repository):
    key = repository.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:48]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    prefix = f"runnerops-autoscale-{slug}-{digest}"
    return {"service": prefix + ".service", "timer": prefix + ".timer"}


def unit_directory():
    return _xdg_config_home() / "systemd" / "user"


def _unit_quote(value):
    """Quote values for systemd unit parsing without allowing substitutions."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$").replace("%", "%%") + '"'


def _environment():
    home = os.environ.get("HOME", str(Path.home()))
    config_home = os.environ.get("XDG_CONFIG_HOME", str(_xdg_config_home()))
    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    cache_home = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    state_home = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    values = {
        "HOME": home,
        "XDG_CONFIG_HOME": config_home,
        "XDG_DATA_HOME": data_home,
        "XDG_CACHE_HOME": cache_home,
        "XDG_STATE_HOME": state_home,
        "ACTIONS_RUNNERS_HOME": os.environ.get("ACTIONS_RUNNERS_HOME", ""),
        "ACTIONS_RUNNERS_ENV": os.environ.get("ACTIONS_RUNNERS_ENV", ""),
        "RUNNERS_CONFIG": os.environ.get("RUNNERS_CONFIG", ""),
        "RUNNER_DATA_ROOT": os.environ.get("RUNNER_DATA_ROOT", ""),
        "RUNNER_CACHE_ROOT": os.environ.get("RUNNER_CACHE_ROOT", ""),
        "RUNNER_STATE_ROOT": str(_state_root()),
        "RUNNER_BOOT_POLICY": os.environ.get("RUNNER_BOOT_POLICY", "on-demand"),
    }
    return {key: value for key, value in values.items() if value}


def unit_contents(repository, runnerctl_path, interval):
    identity = unit_identity(repository)
    environment = _environment()
    service = [
        "[Unit]",
        f"Description=RunnerOps governed autoscale for {repository}",
        "",
        "[Service]",
        "Type=oneshot",
        "Environment=RUNNER_AUTOSCALE_ENABLED=true",
        f"Environment={_unit_quote('RUNNEROPS_CANONICAL_REPOSITORY=' + repository)}",
    ]
    service.extend(f"Environment={_unit_quote(key + '=' + value)}" for key, value in sorted(environment.items()))
    service.extend(
        [
            f"ExecStart={_unit_quote(str(runnerctl_path))} autoscale run-once {repository} --json",
            "",
        ]
    )
    timer = [
        "[Unit]",
        f"Description=RunnerOps autoscale schedule for {repository}",
        "",
        "[Timer]",
        "OnBootSec=10s",
        f"OnUnitActiveSec={interval}s",
        "AccuracySec=1s",
        "Persistent=true",
        f"Unit={identity['service']}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ]
    return {identity["service"]: "\n".join(service), identity["timer"]: "\n".join(timer)}


def _runnerctl_path():
    value = os.environ.get("RUNNERCTL_EXECUTABLE", "")
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_file():
        raise SchedulerError("RUNNERCTL_EXECUTABLE_UNAVAILABLE", 3)
    return path


def _systemctl(*args):
    try:
        result = subprocess.run(
            ("systemctl", "--user", *args), capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SchedulerError("SYSTEMD_USER_UNAVAILABLE", 3) from None
    if result.returncode:
        raise SchedulerError("SYSTEMD_USER_COMMAND_FAILED", 3)
    return result.stdout


def _write_if_changed(path, content):
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return True


def scheduler_state(repository, *, strict=False):
    identity = unit_identity(repository)
    state = {
        "enabled": False,
        "active": False,
        "interval_seconds": None,
        "service": identity["service"],
        "timer": identity["timer"],
        "next_run": None,
    }
    try:
        state["interval_seconds"] = interval_seconds()
    except SchedulerError as exc:
        state["error"] = exc.code
        if strict:
            raise
        return state
    try:
        output = _systemctl(
            "show",
            identity["timer"],
            "--property=UnitFileState,ActiveState,NextElapseUSecRealtime",
            "--no-pager",
        )
    except SchedulerError as exc:
        state["error"] = exc.code
        if strict:
            raise
        return state
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    state["enabled"] = properties.get("UnitFileState") in ("enabled", "enabled-runtime")
    state["active"] = properties.get("ActiveState") == "active"
    next_run = properties.get("NextElapseUSecRealtime")
    state["next_run"] = next_run if next_run and next_run != "n/a" else None
    return state


def enable(repository):
    if os.geteuid() == 0:
        raise SchedulerError("SCHEDULER_REQUIRES_NON_ROOT_USER", 3)
    repository = canonical_repository(repository)
    interval = interval_seconds()
    runnerctl_path = _runnerctl_path()
    units = unit_contents(repository, runnerctl_path, interval)
    directory = unit_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, content in units.items():
        _write_if_changed(directory / name, content)
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", unit_identity(repository)["timer"])
    return repository, scheduler_state(repository, strict=True)


def disable(repository):
    repository = existing_scheduler_repository(repository)
    identity = unit_identity(repository)
    # A scheduler that was never enabled has no unit to stop. This makes disable
    # idempotent without asking systemd to operate on an unknown unit.
    if (unit_directory() / identity["timer"]).exists():
        _systemctl("disable", "--now", identity["timer"])
    return repository, scheduler_state(repository)


def status(repository):
    snapshot = capacity.snapshot(repository)
    resolved = snapshot["repository"]["nameWithOwner"] or snapshot["repository"]["match_key"]
    scheduler = scheduler_state(resolved) if resolved else {
        "enabled": False, "active": False, "interval_seconds": None,
        "service": None, "timer": None, "next_run": None,
        "error": "REPOSITORY_UNRESOLVED",
    }
    snapshot["scheduler"] = scheduler
    return snapshot


def render_status(result):
    capacity.render(result)
    scheduler = result["scheduler"]
    interval = (
        str(scheduler["interval_seconds"]) + "s"
        if scheduler["interval_seconds"] is not None
        else "unknown"
    )
    print(
        "Scheduler: "
        f"enabled={str(scheduler['enabled']).lower()} active={str(scheduler['active']).lower()} "
        f"interval={interval} "
        f"timer={scheduler['timer'] or 'unknown'} next_run={scheduler['next_run'] or 'unknown'}"
    )
    if scheduler.get("error"):
        print(f"  Scheduler evidence: {scheduler['error']}")


def main():
    parser = argparse.ArgumentParser(prog="runnerctl autoscale", description=__doc__)
    parser.add_argument("operation", choices=("enable", "disable", "status"))
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.repository != "." and not re.fullmatch(REPO_PATTERN, args.repository):
        parser.error("expected owner/repo or .")
    try:
        if args.operation == "enable":
            repository, result = enable(args.repository)
            payload = {"repository": repository, "scheduler": result}
        elif args.operation == "disable":
            repository, result = disable(args.repository)
            payload = {"repository": repository, "scheduler": result}
        else:
            payload = status(args.repository)
    except SchedulerError as exc:
        payload = {"status": "error", "diagnostic": exc.code}
        if args.json:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            print(f"Autoscale scheduler: error ({exc.code})", file=sys.stderr)
        return exc.exit_code

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    elif args.operation == "status":
        render_status(payload)
    else:
        scheduler = payload["scheduler"]
        print(
            f"Autoscale scheduler: {args.operation} repository={payload['repository']} "
            f"timer={scheduler['timer']} enabled={str(scheduler['enabled']).lower()} "
            f"active={str(scheduler['active']).lower()} interval={scheduler['interval_seconds']}s"
        )
    return 0 if args.operation != "status" or payload["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
