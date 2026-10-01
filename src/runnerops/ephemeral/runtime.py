#!/usr/bin/env python3
"""Narrow local/GitHub runtime adapters for ephemeral lifecycle reconciliation."""

import json
import os
import pwd
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .contracts import EphemeralAction
from .identity import OWNER_MARKER, disposable_root


class RuntimeFailure(RuntimeError):
    """A bounded runtime operation failed without exposing secret material."""


class GitHubRuntime:
    def __init__(self, gh: str = "gh", timeout: float = 30.0):
        self.gh = gh
        self.timeout = timeout

    def _run(self, args: List[str], operation: str) -> str:
        try:
            result = subprocess.run(
                [self.gh] + args,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeFailure("GitHub operation failed: {}".format(operation)) from error
        return result.stdout

    def request_registration_material(self, repository: str) -> str:
        token = self._run(
            ["api", "--method", "POST",
             "repos/{}/actions/runners/registration-token".format(repository), "--jq", ".token"],
            "registration_material",
        ).strip()
        if not token:
            raise RuntimeFailure("GitHub returned empty registration material")
        return token

    def observe_runner(self, repository: str, identity: str) -> Dict[str, Any]:
        matches = []
        observed = 0
        for page_number in range(1, 101):
            endpoint = (
                "repos/{}/actions/runners?name={}&per_page=100&page={}".format(
                    repository, quote(identity, safe=""), page_number
                )
            )
            output = self._run(
                ["api", "--method", "GET", endpoint],
                "observe_runner_page_{}".format(page_number),
            )
            try:
                page = json.loads(output)
                runners = page["runners"]
                total = page["total_count"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise RuntimeFailure("GitHub runner observation was not valid JSON") from error
            if (not isinstance(page, dict) or not isinstance(runners, list)
                    or type(total) is not int or total < 0
                    or any(not isinstance(runner, dict) for runner in runners)):
                raise RuntimeFailure("GitHub runner observation had an invalid schema")
            observed += len(runners)
            for runner in runners:
                if runner.get("name") == identity:
                    matches.append(runner)
            if len(runners) < 100 or observed >= total:
                break
        else:
            raise RuntimeFailure("GitHub runner observation exceeded bounded pagination")
        if not matches:
            return {"status": "ABSENT", "runner_id": None, "remote_status": None,
                    "ephemeral": None, "busy": False, "reason": "exact_identity_absent"}
        if len(matches) != 1:
            return {"status": "AMBIGUOUS", "runner_id": None, "remote_status": None,
                    "ephemeral": None, "busy": None,
                    "reason": "multiple_exact_identity_matches"}
        runner = matches[0]
        runner_id = runner.get("id")
        busy = runner.get("busy")
        remote_status = runner.get("status")
        ephemeral = runner.get("ephemeral") if "ephemeral" in runner else None
        if type(runner_id) is not int or runner_id <= 0:
            return {"status": "UNKNOWN", "runner_id": runner_id,
                    "remote_status": remote_status, "ephemeral": ephemeral,
                    "busy": busy if isinstance(busy, bool) else None,
                    "reason": "remote_runner_id_invalid"}
        if ephemeral is not None and not isinstance(ephemeral, bool):
            return {"status": "UNKNOWN", "runner_id": runner_id,
                    "remote_status": remote_status, "ephemeral": ephemeral,
                    "busy": busy if isinstance(busy, bool) else None,
                    "reason": "remote_ephemeral_value_invalid"}
        if ephemeral is False:
            return {"status": "UNKNOWN", "runner_id": runner_id,
                    "remote_status": remote_status, "ephemeral": False,
                    "busy": busy if isinstance(busy, bool) else None,
                    "reason": "remote_runner_not_ephemeral"}
        if not isinstance(busy, bool) or remote_status not in ("online", "offline"):
            return {"status": "UNKNOWN", "runner_id": runner_id,
                    "remote_status": remote_status, "ephemeral": ephemeral,
                    "busy": busy if isinstance(busy, bool) else None,
                    "reason": "remote_runner_state_invalid"}
        status = "BUSY" if busy is True else ("ONLINE" if remote_status == "online" else "OFFLINE")
        return {
            "status": status,
            "runner_id": runner_id,
            "remote_status": remote_status,
            "ephemeral": ephemeral,
            "busy": busy,
            "reason": "exact_identity_observed",
        }

    def delete_runner(self, repository: str, runner_id: int) -> None:
        if type(runner_id) is not int or runner_id <= 0:
            raise RuntimeFailure("refusing to delete an invalid GitHub runner id")
        self._run(
            ["api", "--method", "DELETE", "repos/{}/actions/runners/{}".format(repository, runner_id)],
            "delete_runner",
        )


class LocalRuntime:
    def __init__(
        self,
        ephemeral_root: Path,
        package_helper: Path,
        ephemeral_helper: Path,
        systemctl: str = "systemctl",
        command_timeout: float = 120.0,
    ):
        self.ephemeral_root = Path(ephemeral_root).absolute()
        self.package_helper = Path(package_helper)
        self.ephemeral_helper = Path(ephemeral_helper)
        self.systemctl = systemctl
        self.command_timeout = command_timeout
        self.service_user = pwd.getpwuid(os.getuid()).pw_name

    def _run(self, args: List[str], operation: str, input_text: Optional[str] = None) -> str:
        try:
            result = subprocess.run(
                args,
                input=input_text,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.command_timeout,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeFailure("local operation failed: {}".format(operation)) from error
        return result.stdout

    def ensure_package(self, version: str, arch: str) -> Path:
        output = self._run(
            [str(self.package_helper), "ensure", "--version", version, "--arch", arch],
            "ensure_package",
        ).strip()
        path = Path(output)
        if not path.is_file():
            raise RuntimeFailure("verified runner package path is unavailable")
        return path

    def expected_root(self, action_id: str) -> Path:
        return disposable_root(self.ephemeral_root, action_id)

    def systemd_unit(self, action: EphemeralAction) -> str:
        return "actions.runner.runnerops-ephemeral-{}@{}.service".format(
            self.service_user, action.action_id)

    def preflight(self, action: EphemeralAction) -> None:
        self._run(
            [str(self.ephemeral_helper), "check", "--action-id", action.action_id,
             "--identity", action.runner_identity, "--unit", self.systemd_unit(action)],
            "ephemeral_systemd_preflight",
        )

    def allocate_root(self, action: EphemeralAction) -> Path:
        expected = self.expected_root(action.action_id)
        if Path(action.disposable_root).absolute() != expected:
            raise RuntimeFailure("disposable root does not match exact action root")
        self.ephemeral_root.parent.mkdir(parents=True, exist_ok=True)
        if self.ephemeral_root.exists() and self.ephemeral_root.is_symlink():
            raise RuntimeFailure("ephemeral root cannot be a symlink")
        self.ephemeral_root.mkdir(mode=0o700, exist_ok=True)
        if expected.exists():
            self.validate_owned_root(action, allow_missing=False)
            return expected
        expected.mkdir(mode=0o700)
        marker = expected / OWNER_MARKER
        payload = {
            "schema_version": 1,
            "kind": "RunnerOpsEphemeralOwnership",
            "action_id": action.action_id,
            "runner_identity": action.runner_identity,
            "disposable_root": str(expected),
        }
        try:
            descriptor = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
        except Exception:
            shutil.rmtree(str(expected))
            raise
        return expected

    def materialize(self, action: EphemeralAction, package: Path) -> None:
        self.validate_owned_root(action, allow_missing=False)
        self._run(
            [
                str(self.ephemeral_helper), "prepare",
                "--root", action.disposable_root,
                "--action-id", action.action_id,
                "--identity", action.runner_identity,
                "--package", str(package),
            ],
            "prepare_ephemeral",
        )

    def configure(self, action: EphemeralAction, registration_material: str) -> None:
        self.validate_owned_root(action, allow_missing=False)
        self._run(
            [
                str(self.ephemeral_helper), "configure",
                "--root", action.disposable_root,
                "--action-id", action.action_id,
                "--identity", action.runner_identity,
                "--repo-url", "https://github.com/{}".format(action.repository),
                "--labels", ",".join(action.labels),
            ],
            "configure_ephemeral",
            input_text=registration_material + "\n",
        )
        registration = self._local_config_registration(Path(action.disposable_root))
        if registration["identity"] != action.runner_identity:
            raise RuntimeFailure("local runner configuration does not prove the exact identity")
        if type(registration["runner_id"]) is not int or registration["runner_id"] <= 0:
            raise RuntimeFailure("local runner configuration does not contain a valid runner id")

    def start(self, action: EphemeralAction) -> Dict[str, Any]:
        self.validate_owned_root(action, allow_missing=False)
        output = self._run(
            [
                str(self.ephemeral_helper), "start",
                "--root", action.disposable_root,
                "--action-id", action.action_id,
                "--identity", action.runner_identity,
                "--unit", self.systemd_unit(action),
            ],
            "start_ephemeral",
        ).strip()
        if output != self.systemd_unit(action):
            raise RuntimeFailure("ephemeral helper returned an unexpected systemd unit")
        observation = self.observe(action)
        if observation["status"] not in ("RUNNING", "STARTING"):
            raise RuntimeFailure("ephemeral systemd unit did not become active after start")
        return observation

    def _systemd_observation(self, unit: str) -> Dict[str, Any]:
        output = self._run(
            [self.systemctl, "show", unit, "--no-pager",
             "--property=LoadState", "--property=ActiveState", "--property=SubState",
             "--property=Result", "--property=MainPID",
             "--property=ExecMainStartTimestampMonotonic"],
            "observe_ephemeral_systemd_unit",
        )
        properties = {}
        for line in output.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value
        active = properties.get("ActiveState")
        if properties.get("LoadState") == "not-found":
            status = "NOT_STARTED"
        elif active == "active":
            status = "RUNNING"
        elif active == "activating":
            status = "STARTING"
        elif active in ("inactive", "failed"):
            started = properties.get("ExecMainStartTimestampMonotonic")
            status = "EXITED" if active == "failed" or (started and started != "0") else "NOT_STARTED"
        elif active == "deactivating":
            status = "STOPPING"
        else:
            status = "UNKNOWN"
        main_pid = properties.get("MainPID")
        started = properties.get("ExecMainStartTimestampMonotonic")
        return {
            "status": status,
            "systemd_unit": unit,
            "active_state": active,
            "sub_state": properties.get("SubState"),
            "service_result": properties.get("Result"),
            "main_pid": int(main_pid) if main_pid and main_pid.isdigit() and main_pid != "0" else None,
            "started_at_monotonic": int(started) if started and started.isdigit() and started != "0" else None,
        }

    @staticmethod
    def _local_config_registration(root: Path) -> Dict[str, Any]:
        config = root / ".runner"
        if config.is_symlink() or not config.is_file():
            return {"identity": None, "runner_id": None}
        try:
            value = json.loads(config.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {"identity": "__invalid__", "runner_id": "__invalid__"}
        if not isinstance(value, dict):
            return {"identity": "__invalid__", "runner_id": "__invalid__"}
        identity = value.get("agentName")
        runner_id = value.get("agentId")
        return {
            "identity": identity if isinstance(identity, str) and identity else "__invalid__",
            "runner_id": runner_id if type(runner_id) is int and runner_id > 0 else "__invalid__",
        }

    def observe(self, action: EphemeralAction) -> Dict[str, Any]:
        root = Path(action.disposable_root)
        config_present = (root / ".runner").is_file() if root.is_dir() and not root.is_symlink() else False
        registration = self._local_config_registration(root) if config_present else {
            "identity": None, "runner_id": None,
        }
        config_identity = registration["identity"]
        config_runner_id = registration["runner_id"]
        unit = self.systemd_unit(action)
        systemd = self._systemd_observation(unit)
        start_observed = bool(action.local_observation.get("start_observed"))
        if systemd["status"] in ("RUNNING", "STARTING", "STOPPING", "EXITED"):
            start_observed = True
        elif systemd["status"] == "NOT_STARTED" and start_observed:
            systemd["status"] = "EXITED"
        systemd["start_observed"] = start_observed
        if (action.action_state == "CLEANED" and not root.exists()
                and systemd["status"] in ("EXITED", "NOT_STARTED")):
            systemd.update({"status": "CLEANED", "config_present": False,
                            "config_identity": None, "config_runner_id": None,
                            "reason": "root_absent_and_exact_systemd_unit_inactive"})
            return systemd
        if config_present and config_identity != action.runner_identity:
            systemd.update({"status": "UNKNOWN", "config_present": True,
                            "config_identity": config_identity,
                            "config_runner_id": config_runner_id,
                            "reason": "local_configuration_identity_mismatch"})
            return systemd
        if config_present and (type(config_runner_id) is not int or config_runner_id <= 0):
            systemd.update({"status": "UNKNOWN", "config_present": True,
                            "config_identity": config_identity,
                            "config_runner_id": config_runner_id,
                            "reason": "local_configuration_runner_id_invalid"})
            return systemd
        if systemd["status"] != "NOT_STARTED":
            systemd.update({"config_present": config_present,
                            "config_identity": config_identity,
                            "config_runner_id": config_runner_id,
                            "reason": "exact_systemd_unit_observed"})
            return systemd
        if root.exists():
            return {"status": "CONFIGURED" if config_present else "ALLOCATED",
                    "systemd_unit": unit, "active_state": None, "sub_state": None,
                    "service_result": None, "main_pid": None,
                    "started_at_monotonic": None,
                    "start_observed": start_observed,
                    "config_present": config_present, "config_identity": config_identity,
                    "config_runner_id": config_runner_id,
                    "reason": "no_process_recorded"}
        return {"status": "ABSENT", "systemd_unit": unit, "active_state": None,
                "sub_state": None, "service_result": None, "main_pid": None,
                "started_at_monotonic": None,
                "start_observed": start_observed,
                "config_present": False, "config_identity": None,
                "config_runner_id": None,
                "reason": "disposable_root_absent"}

    def stop(self, action: EphemeralAction, timeout: float = 10.0) -> None:
        observation = self.observe(action)
        if observation["status"] not in ("RUNNING", "STARTING", "STOPPING"):
            return
        if observation["status"] != "STOPPING":
            self._run(
                [str(self.ephemeral_helper), "stop", "--root", action.disposable_root,
                 "--action-id", action.action_id, "--identity", action.runner_identity,
                 "--unit", self.systemd_unit(action)],
                "stop_ephemeral",
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.observe(action)["status"] not in ("RUNNING", "STARTING", "STOPPING"):
                return
            time.sleep(0.1)
        if self.observe(action)["status"] in ("RUNNING", "STARTING", "STOPPING"):
            raise RuntimeFailure("exact ephemeral systemd unit did not stop within bound")

    def validate_owned_root(self, action: EphemeralAction, allow_missing: bool = True) -> Path:
        expected = self.expected_root(action.action_id)
        recorded = Path(action.disposable_root).absolute()
        if recorded != expected:
            raise RuntimeFailure("cleanup target is not the exact derived ephemeral root")
        if self.ephemeral_root.is_symlink() or expected.is_symlink():
            raise RuntimeFailure("symlink cleanup target is unsafe")
        if not expected.exists():
            if allow_missing:
                return expected
            raise RuntimeFailure("owned disposable root is missing")
        if not expected.is_dir():
            raise RuntimeFailure("disposable root is not a directory")
        parent_real = Path(os.path.realpath(str(self.ephemeral_root)))
        target_real = Path(os.path.realpath(str(expected)))
        if target_real.parent != parent_real:
            raise RuntimeFailure("disposable root escapes the ephemeral root")
        marker = expected / OWNER_MARKER
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeFailure("ephemeral ownership marker is missing or unsafe")
        try:
            ownership = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeFailure("ephemeral ownership marker is unreadable") from error
        expected_ownership = {
            "schema_version": 1,
            "kind": "RunnerOpsEphemeralOwnership",
            "action_id": action.action_id,
            "runner_identity": action.runner_identity,
            "disposable_root": str(expected),
        }
        if ownership != expected_ownership:
            raise RuntimeFailure("ephemeral ownership proof does not match action")
        return expected

    def remove_root(self, action: EphemeralAction) -> None:
        root = self.validate_owned_root(action, allow_missing=True)
        if not root.exists():
            return
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise RuntimeFailure("platform cannot provide symlink-safe recursive cleanup")
        shutil.rmtree(str(root))
        if root.exists():
            raise RuntimeFailure("disposable root remained after cleanup")
