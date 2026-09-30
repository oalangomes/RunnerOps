#!/usr/bin/env python3
"""Filesystem ownership and fail-closed cleanup contracts."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from runnerops.ephemeral.contracts import EphemeralAction
from runnerops.ephemeral.identity import disposable_root, runner_identity
from runnerops.ephemeral.runtime import LocalRuntime, RuntimeFailure


ACTION_ID = "22222222222222222222222222222222"


class CleanupSafetyContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.ephemeral_root = self.base / "data" / "runners" / ".ephemeral"
        helper = self.base / "unused-helper"
        self.runtime = LocalRuntime(self.ephemeral_root, helper, helper)
        now = datetime.now(timezone.utc).isoformat()
        self.action = EphemeralAction(
            action_id=ACTION_ID,
            runner_identity=runner_identity(ACTION_ID),
            disposable_root=str(disposable_root(self.ephemeral_root, ACTION_ID)),
            repository="Example/Repo", profile="generic", labels=["ephemeral"],
            created_at=now, updated_at=now,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_ownership_is_required_and_repeated_removal_is_safe(self):
        persistent_state = self.base / "state" / "persistent-state"
        persistent_state.parent.mkdir(parents=True)
        persistent_state.write_text("keep", encoding="utf-8")
        root = self.runtime.allocate_root(self.action)
        (root / "artifact").write_text("disposable", encoding="utf-8")
        self.runtime.remove_root(self.action)
        self.runtime.remove_root(self.action)
        self.assertFalse(root.exists())
        self.assertEqual(persistent_state.read_text(encoding="utf-8"), "keep")

    def test_persistent_runner_root_cannot_be_cleanup_target(self):
        persistent = self.base / "data" / "runners" / "persistent-one"
        persistent.mkdir(parents=True)
        (persistent / "keep").write_text("untouched", encoding="utf-8")
        self.action.disposable_root = str(persistent)
        with self.assertRaisesRegex(RuntimeFailure, "exact derived ephemeral root"):
            self.runtime.remove_root(self.action)
        self.assertEqual((persistent / "keep").read_text(encoding="utf-8"), "untouched")

    def test_missing_or_wrong_ownership_marker_fails_closed(self):
        root = Path(self.action.disposable_root)
        root.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeFailure, "ownership marker"):
            self.runtime.remove_root(self.action)
        (root / ".runnerops-ephemeral-owner.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeFailure, "ownership proof"):
            self.runtime.remove_root(self.action)

    def test_symlink_root_escape_is_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "keep").write_text("safe", encoding="utf-8")
        self.ephemeral_root.mkdir(parents=True)
        os.symlink(outside, self.action.disposable_root)
        with self.assertRaisesRegex(RuntimeFailure, "symlink cleanup target"):
            self.runtime.remove_root(self.action)
        self.assertTrue((outside / "keep").exists())

    def test_local_observation_uses_exact_systemd_unit_as_authority(self):
        fake_systemctl = self.base / "systemctl"
        fake_systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' 'LoadState=loaded' 'ActiveState=active' 'SubState=running' "
            "'Result=success' 'MainPID=4242' 'ExecMainStartTimestampMonotonic=123456'\n",
            encoding="utf-8",
        )
        fake_systemctl.chmod(0o755)
        self.runtime.systemctl = str(fake_systemctl)
        self.runtime.allocate_root(self.action)
        observation = self.runtime.observe(self.action)
        self.assertEqual(observation["status"], "RUNNING")
        self.assertEqual(observation["active_state"], "active")
        self.assertEqual(observation["main_pid"], 4242)
        self.assertIn("@{}.service".format(ACTION_ID), observation["systemd_unit"])

    def test_local_configuration_identity_mismatch_is_inconclusive(self):
        fake_systemctl = self.base / "systemctl-missing"
        fake_systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' 'LoadState=not-found' 'ActiveState=inactive' 'SubState=dead' "
            "'Result=success' 'MainPID=0'\n",
            encoding="utf-8",
        )
        fake_systemctl.chmod(0o755)
        self.runtime.systemctl = str(fake_systemctl)
        root = self.runtime.allocate_root(self.action)
        (root / ".runner").write_text(
            '{"agentId":42,"agentName":"some-other-runner"}\n', encoding="utf-8")
        observation = self.runtime.observe(self.action)
        self.assertEqual(observation["status"], "UNKNOWN")
        self.assertEqual(observation["reason"], "local_configuration_identity_mismatch")
        self.assertEqual(observation["config_identity"], "some-other-runner")

    def test_cleaned_action_remains_cleaned_when_unit_is_inactive_and_root_absent(self):
        fake_systemctl = self.base / "systemctl-inactive"
        fake_systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' 'LoadState=loaded' 'ActiveState=inactive' 'SubState=dead' "
            "'Result=success' 'MainPID=0'\n",
            encoding="utf-8",
        )
        fake_systemctl.chmod(0o755)
        self.runtime.systemctl = str(fake_systemctl)
        self.action.action_state = "CLEANED"
        observation = self.runtime.observe(self.action)
        self.assertEqual(observation["status"], "CLEANED")
        self.assertFalse(observation["config_present"])


if __name__ == "__main__":
    unittest.main()
