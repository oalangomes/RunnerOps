#!/usr/bin/env python3
"""Contract tests for #102's user-systemd scheduling boundary."""

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import autoscale_planner as planner  # noqa: E402
import autoscale_scheduler as scheduler  # noqa: E402


class SchedulerContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "systemctl.log"
        self.state = self.root / "systemctl-state"
        self.registry = self.root / "config" / "actions-runners" / "runners.conf"
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text("# local machine registry\nci-a|/tmp/ci-a|generic|example/project|true|example\n")
        self.runnerctl = self.root / "arbitrary install" / "runnerctl"
        self.runnerctl.parent.mkdir()
        self.runnerctl.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.runnerctl.chmod(0o755)
        self._write_fake_commands()
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": str(self.bin) + os.pathsep + self.env["PATH"],
                "HOME": str(self.root / "home"),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "RUNNER_STATE_ROOT": str(self.root / "state" / "actions-runners"),
                "RUNNERS_CONFIG": str(self.registry),
                "ACTIONS_RUNNERS_HOME": str(ROOT),
                "ACTIONS_RUNNERS_ENV": str(self.root / "config" / "actions-runners" / "config.env"),
                "RUNNER_BOOT_POLICY": "on-demand",
                "RUNNERCTL_EXECUTABLE": str(self.runnerctl),
                "TEST_SYSTEMCTL_LOG": str(self.log),
                "TEST_SYSTEMCTL_STATE": str(self.state),
                "RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS": "300",
            }
        )

    def tearDown(self):
        self.temp.cleanup()

    def _write_fake_commands(self):
        systemctl = self.bin / "systemctl"
        systemctl.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--user" ]] || exit 70
shift
printf '%s\\n' "$*" >> "${TEST_SYSTEMCTL_LOG:?}"
case "${1:-}" in
  daemon-reload) ;;
  enable) printf 'enabled active\\n' > "${TEST_SYSTEMCTL_STATE:?}" ;;
  disable) printf 'disabled inactive\\n' > "${TEST_SYSTEMCTL_STATE:?}" ;;
  show)
    read -r enabled active < "${TEST_SYSTEMCTL_STATE:?}" 2>/dev/null || { enabled=disabled; active=inactive; }
    printf 'UnitFileState=%s\\nActiveState=%s\\nNextElapseUSecRealtime=Thu 2026-01-01 00:01:00 UTC\\n' "$enabled" "$active"
    ;;
  *) exit 71 ;;
esac
"""
        )
        gh = self.bin / "gh"
        gh.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "repo" && "${2:-}" == "view" ]] || exit 72
printf '%s\\n' 'Example/Project'
"""
        )
        for path in (systemctl, gh):
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_cli(self, *args, extra_env=None):
        env = self.env.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "autoscale_scheduler.py"), *args],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_enable_writes_deterministic_user_units_and_is_idempotent(self):
        result = self.run_cli("enable", "example/project", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["repository"], "Example/Project")
        self.assertEqual(payload["scheduler"]["interval_seconds"], 60)
        self.assertTrue(payload["scheduler"]["enabled"])
        self.assertTrue(payload["scheduler"]["active"])

        identity = scheduler.unit_identity("Example/Project")
        unit_dir = self.root / "config" / "systemd" / "user"
        service = (unit_dir / identity["service"]).read_text()
        timer = (unit_dir / identity["timer"]).read_text()
        self.assertIn("Environment=RUNNER_AUTOSCALE_ENABLED=true", service)
        self.assertIn(
            'Environment="RUNNEROPS_CANONICAL_REPOSITORY=Example/Project"',
            service,
        )
        policy_file = (
            Path(self.env["RUNNER_STATE_ROOT"])
            / "autoscale-scheduler"
            / (identity["service"] + ".env")
        )
        self.assertIn(
            f'Environment="RUNNEROPS_AUTOSCALE_POLICY_FILE={policy_file}"',
            service,
        )
        self.assertTrue(policy_file.is_file())
        self.assertEqual(policy_file.stat().st_mode & 0o777, 0o600)
        self.assertIn(f'ExecStart="{self.runnerctl}" autoscale run-once Example/Project --json', service)
        self.assertIn("OnActiveSec=10s", timer)
        self.assertIn("OnUnitActiveSec=60s", timer)
        self.assertNotIn("OnBootSec=", timer)
        self.assertNotIn("Persistent=true", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertNotIn("ensure", service.lower() + timer.lower())
        self.assertNotIn("start all", service.lower() + timer.lower())
        self.assertNotIn("group:", service.lower() + timer.lower())
        self.assertNotIn("sudo", service.lower() + timer.lower())
        self.assertIn(f'Environment="RUNNERS_CONFIG={self.registry}"', service)
        self.assertIn(f'Environment="RUNNER_STATE_ROOT={self.env["RUNNER_STATE_ROOT"]}"', service)

        original = service
        repeated = self.run_cli("enable", "EXAMPLE/PROJECT", "--json")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual((unit_dir / identity["service"]).read_text(), original)
        self.assertEqual(
            self.log.read_text().splitlines(),
            [
                "daemon-reload",
                f"enable --now {identity['timer']}",
                f"show {identity['timer']} --property=UnitFileState,ActiveState,NextElapseUSecRealtime --no-pager",
                "daemon-reload",
                f"enable --now {identity['timer']}",
                f"show {identity['timer']} --property=UnitFileState,ActiveState,NextElapseUSecRealtime --no-pager",
            ],
        )

    def test_enable_persists_normalized_policy_and_overrides_later_config(self):
        requested = {
            "RUNNER_AUTOSCALE_QUEUE_THRESHOLD_SECONDS": "123",
            "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS": "7",
            "RUNNER_AUTOSCALE_MIN_MEMORY_AVAILABLE_MIB": "2048",
            "RUNNER_AUTOSCALE_MAX_CPU_PERCENT": "81.5",
            "RUNNER_AUTOSCALE_COOLDOWN_SECONDS": "45",
            "RUNNER_AUTOSCALE_LOCAL_SCALE_OUT_COOLDOWN_SECONDS": "12",
            "RUNNER_AUTOSCALE_LABEL_SCOPE": "self-hosted,Linux,local-runner",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "true",
            "RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS": "9",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE": "python",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP": "Example-Team",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS": "self-hosted,Linux,X64,python,local-runner",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX": "Example-Auto",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_VERSION": "latest",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_ARCH": "x64",
        }
        expected_env = self.env.copy()
        expected_env.update(requested)
        previous = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(expected_env)
            expected_fingerprint = planner.policy_fingerprint(planner.load_policy())
        finally:
            os.environ.clear()
            os.environ.update(previous)

        result = self.run_cli("enable", "example/project", "--json", extra_env=requested)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy_file = (
            Path(self.env["RUNNER_STATE_ROOT"])
            / "autoscale-scheduler"
            / (scheduler.unit_identity("Example/Project")["service"] + ".env")
        )
        contents = policy_file.read_text(encoding="utf-8")
        self.assertIn("RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS=7", contents)
        self.assertIn("RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED=true", contents)
        self.assertIn("RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP=example-team", contents)
        self.assertIn("RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX=example-auto", contents)

        config = Path(self.env["ACTIONS_RUNNERS_ENV"])
        config.write_text(
            "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS=99\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED=false\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP=wrong-group\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX=wrong-prefix\n"
            "RUNNEROPS_AUTOSCALE_POLICY_FILE=/tmp/config-must-not-win.env\n",
            encoding="utf-8",
        )

        runtime_env = self.env.copy()
        runtime_env["RUNNEROPS_AUTOSCALE_POLICY_FILE"] = str(policy_file)
        probe = subprocess.run(
            [
                "bash",
                "-c",
                (
                    f'source "{ROOT / "runner-runtime-env.sh"}"; '
                    f'PYTHONPATH="{ROOT}" python3 -c '
                    "'from autoscale_planner import load_policy, policy_fingerprint; "
                    "print(policy_fingerprint(load_policy()))'"
                ),
            ],
            env=runtime_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.strip(), expected_fingerprint)

    def test_explicit_cli_autoscale_env_overrides_machine_config(self):
        config = Path(self.env["ACTIONS_RUNNERS_ENV"])
        config.write_text(
            "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS=99\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED=true\n"
            "RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS=99\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE=python\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP=config-group\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS=self-hosted,Linux,X64,python,local-runner\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX=config-auto\n",
            encoding="utf-8",
        )
        probe_env = self.env.copy()
        probe_env.update(
            {
                "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS": "0",
                "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "false",
            }
        )
        probe = subprocess.run(
            [
                "bash",
                "-c",
                (
                    f'source "{ROOT / "runner-runtime-env.sh"}"; '
                    "printf '%s|%s' "
                    '"$RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS" '
                    '"$RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED"'
                ),
            ],
            env=probe_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout, "0|false")

    def test_managed_scheduler_policy_overrides_cli_and_machine_config(self):
        requested = {
            "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS": "7",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "false",
        }
        result = self.run_cli("enable", "example/project", "--json", extra_env=requested)
        self.assertEqual(result.returncode, 0, result.stderr)
        identity = scheduler.unit_identity("Example/Project")
        policy_file = (
            Path(self.env["RUNNER_STATE_ROOT"])
            / "autoscale-scheduler"
            / (identity["service"] + ".env")
        )
        config = Path(self.env["ACTIONS_RUNNERS_ENV"])
        config.write_text(
            "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS=99\n"
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED=true\n",
            encoding="utf-8",
        )
        probe_env = self.env.copy()
        probe_env.update(
            {
                "RUNNEROPS_AUTOSCALE_POLICY_FILE": str(policy_file),
                "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS": "123",
                "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "true",
            }
        )
        probe = subprocess.run(
            [
                "bash",
                "-c",
                (
                    f'source "{ROOT / "runner-runtime-env.sh"}"; '
                    "printf '%s|%s' "
                    '"$RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS" '
                    '"$RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED"'
                ),
            ],
            env=probe_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout, "7|false")

    def test_enable_rejects_invalid_autoscale_policy_before_installing_units(self):
        result = self.run_cli(
            "enable",
            "example/project",
            "--json",
            extra_env={
                "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "true",
                "RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS": "0",
            },
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["diagnostic"], "INVALID_AUTOSCALE_POLICY")
        self.assertFalse((self.root / "config" / "systemd" / "user").exists())

    def test_interval_must_be_positive_and_below_queue_gap(self):
        for interval, diagnostic in (("0", "INVALID_SCHEDULER_INTERVAL"), ("300", "INTERVAL_MUST_BE_BELOW_QUEUE_GAP")):
            result = self.run_cli("enable", "example/project", "--json", extra_env={"RUNNER_AUTOSCALE_INTERVAL_SECONDS": interval})
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["diagnostic"], diagnostic)
        self.assertFalse((self.root / "config" / "systemd" / "user").exists())

    def test_disable_is_idempotent_and_does_not_touch_runner_configuration(self):
        self.assertEqual(self.run_cli("enable", "example/project").returncode, 0)
        registry_digest = hashlib.sha256(self.registry.read_bytes()).hexdigest()
        boot_policy = self.env["RUNNER_BOOT_POLICY"]
        first = self.run_cli("disable", "example/project", "--json")
        second = self.run_cli("disable", "example/project", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(json.loads(second.stdout)["scheduler"]["enabled"])
        self.assertFalse(json.loads(second.stdout)["scheduler"]["active"])
        self.assertEqual(hashlib.sha256(self.registry.read_bytes()).hexdigest(), registry_digest)
        self.assertEqual(self.env["RUNNER_BOOT_POLICY"], boot_policy)
        self.assertEqual(self.log.read_text().count("disable --now"), 2)

    def test_disable_keeps_safety_exit_available_without_github_authentication(self):
        self.assertEqual(self.run_cli("enable", "example/project").returncode, 0)
        (self.bin / "gh").write_text("#!/usr/bin/env bash\nexit 1\n")
        (self.bin / "gh").chmod(0o755)
        result = self.run_cli("disable", "example/project", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["scheduler"]["enabled"])

    def test_status_keeps_capacity_payload_and_adds_scheduler_state_for_humans_and_json(self):
        self.assertEqual(self.run_cli("enable", "example/project").returncode, 0)
        snapshot = {
            "schema_version": 1,
            "status": "complete",
            "repository": {"requested": "example/project", "nameWithOwner": "Example/Project", "match_key": "example/project"},
            "queue": {"queued_job_count": 0, "observed_queued_job_count": 0, "jobs": []},
            "capacity": {"counts": {}, "runners": []},
            "host": {"active_local_runner_count": 0},
            "errors": [],
        }
        with mock.patch.object(scheduler.capacity, "snapshot", return_value=snapshot):
            previous = os.environ.copy()
            try:
                os.environ.clear()
                os.environ.update(self.env)
                result = scheduler.status("example/project")
                self.assertEqual(result["schema_version"], 1)
                self.assertEqual(result["queue"]["queued_job_count"], 0)
                self.assertTrue(result["scheduler"]["enabled"])
                output = io.StringIO()
                with redirect_stdout(output):
                    scheduler.render_status(result)
                self.assertIn("Scheduler: enabled=true active=true interval=60s", output.getvalue())
            finally:
                os.environ.clear()
                os.environ.update(previous)


if __name__ == "__main__":
    unittest.main(verbosity=2)
