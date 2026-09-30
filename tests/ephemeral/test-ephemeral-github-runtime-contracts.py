#!/usr/bin/env python3
"""Contracts for bounded GitHub runner observation through compatible gh calls."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from runnerops.ephemeral.runtime import GitHubRuntime, RuntimeFailure


IDENTITY = "runnerops-ephemeral-0123456789abcdef"


class GitHubRuntimeContracts(unittest.TestCase):
    @patch("runnerops.ephemeral.runtime.subprocess.run")
    def test_observation_uses_bounded_explicit_get_pages_without_gh_slurp(self, run):
        first_page = {
            "total_count": 101,
            "runners": [
                {"id": number, "name": "other-{}".format(number),
                 "status": "offline", "busy": False}
                for number in range(100)
            ],
        }
        second_page = {
            "total_count": 101,
            "runners": [
                {"id": 4242, "name": IDENTITY, "status": "online", "busy": False}
            ],
        }
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=json.dumps(first_page), stderr=""),
            subprocess.CompletedProcess([], 0, stdout=json.dumps(second_page), stderr=""),
        ]

        observation = GitHubRuntime().observe_runner("Example/Repo", IDENTITY)

        self.assertEqual(observation["status"], "ONLINE")
        self.assertEqual(observation["runner_id"], 4242)
        self.assertEqual(run.call_count, 2)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all("--method" in command and "GET" in command for command in commands))
        self.assertTrue(all("--slurp" not in command and "--paginate" not in command
                            for command in commands))
        self.assertIn("page=1", commands[0][-1])
        self.assertIn("page=2", commands[1][-1])

    @patch("runnerops.ephemeral.runtime.subprocess.run")
    def test_invalid_runner_collection_is_inconclusive_not_absent(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"total_count": 1, "runners": "invalid"}), stderr=""
        )

        with self.assertRaisesRegex(RuntimeFailure, "invalid schema"):
            GitHubRuntime().observe_runner("Example/Repo", IDENTITY)


if __name__ == "__main__":
    unittest.main()
