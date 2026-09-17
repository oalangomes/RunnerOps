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
        self.assertIn(f'ExecStart="{self.runnerctl}" autoscale run-once Example/Project --json', service)
        self.assertIn("OnUnitActiveSec=60s", timer)
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
