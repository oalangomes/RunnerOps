#!/usr/bin/env python3
"""Contracts for immutable autoscale runner names across the add boundary."""

import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_provision import provision_exact  # noqa: E402


class ExactProvisioningContracts(unittest.TestCase):
    target = "runnerops-auto-01"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def policy(self):
        return {
            "enabled": True,
            "max_local_runners": 4,
            "template": {
                "profile": "python",
                "group": "runnerops",
                "labels": ["Linux", "X64", "runnerops", "self-hosted"],
                "name_prefix": "runnerops-auto",
                "runner_version": "latest",
                "runner_arch": "auto",
            },
        }

    def fake_runnerctl(self):
        path = self.base / "runnerctl-fake"
        log = self.base / "runnerctl-env.log"
        path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"log={str(log)!r}\n"
            "if [[ \" $* \" == *\" --plan \"* ]]; then\n"
            f"  printf '%s\\n' '- runner name: {self.target}'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' \"${RUNNEROPS_EXACT_NAME:-missing}\" > \"$log\"\n"
            f"printf '%s\\n' '[OK] runner={self.target} repo=Example/Project policy=on-demand'\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path, log

    def make_runner_tar(self):
        fixture = self.base / "tar-fixture"
        fixture.mkdir()
        marker = self.base / "config-called"
        config = fixture / "config.sh"
        config.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"printf '%s\\n' called > {str(marker)!r}\n"
            "printf '%s\\n' '{\"agentId\":42,\"agentName\":\"fake\"}' > .runner\n",
            encoding="utf-8",
        )
        config.chmod(0o755)
        archive = self.base / "fake-runner.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(config, arcname="config.sh")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return archive, digest, marker

    def configure_env(self, registry, runner_root):
        env = os.environ.copy()
        env.update(
            {
                "ACTIONS_RUNNERS_ENV": str(self.base / "missing.env"),
                "RUNNERS_CONFIG": str(registry),
                "RUNNER_DATA_ROOT": str(runner_root),
                "RUNNER_CACHE_ROOT": str(self.base / "cache"),
                "RUNNER_STATE_ROOT": str(self.base / "state"),
                "RUNNEROPS_EXACT_NAME": self.target,
            }
        )
        return env

    def configure_command(self, archive, digest):
        return [
            str(ROOT / "configure-runner.sh"),
            "--repo-url",
            "https://github.com/Example/Project",
            "--token-stdin",
            "--name",
            self.target,
            "--profile",
            "python",
            "--group",
            "runnerops",
            "--labels",
            "self-hosted,Linux,X64,runnerops",
            "--runner-tar",
            str(archive),
            "--expected-sha256",
            digest,
        ]

    def test_provision_exact_exports_immutable_target_to_apply(self):
        runnerctl, log = self.fake_runnerctl()
        result = provision_exact(
            "Example/Project",
            self.target,
            self.policy(),
            runnerctl=runnerctl,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["code"], "PROVISION_ADD_COMPLETED")
        self.assertEqual(log.read_text(encoding="utf-8").strip(), self.target)

    def test_exact_collision_fails_without_auto_increment_or_config_execution(self):
        archive, digest, marker = self.make_runner_tar()
        registry = self.base / "collision-runners.conf"
        runner_root = self.base / "collision-runners"
        runner_root.mkdir()
        registry.write_text(
            "# name|path|profile|repo|enabled|group\n"
            f"{self.target}|/tmp/existing|python|Example/Project|true|runnerops\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            self.configure_command(archive, digest),
            input="short-lived-token\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.configure_env(registry, runner_root),
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("[EXACT-NAME-COLLISION]", output)
        self.assertFalse(marker.exists())
        self.assertFalse((runner_root / f"{self.target}-2").exists())
        self.assertNotIn(f"{self.target}-2|", registry.read_text(encoding="utf-8"))

    def test_exact_success_persists_exact_target_without_suffix(self):
        archive, digest, marker = self.make_runner_tar()
        registry = self.base / "success-runners.conf"
        runner_root = self.base / "success-runners"

        completed = subprocess.run(
            self.configure_command(archive, digest),
            input="short-lived-token\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.configure_env(registry, runner_root),
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertTrue(marker.exists())
        self.assertTrue((runner_root / self.target / ".runner").is_file())
        self.assertFalse((runner_root / f"{self.target}-2").exists())
        rows = [
            line
            for line in registry.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith(f"{self.target}|"), rows[0])
        self.assertIn(f"Nome local: {self.target}", output)


if __name__ == "__main__":
    unittest.main()
