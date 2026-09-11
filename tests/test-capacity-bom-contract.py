#!/usr/bin/env python3
"""Regression contract for BOM-prefixed GitHub runner registration metadata."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import capacity  # noqa: E402


class CapacityBomContract(unittest.TestCase):
    def test_collect_local_accepts_utf8_bom_runner_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = root / "runner-1"
            runner.mkdir()
            (runner / ".runner").write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "agentId": 42,
                        "agentName": "host-runner-1",
                        "gitHubUrl": "https://github.com/example/repo",
                    }
                ),
                encoding="utf-8",
            )
            (runner / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (runner / "run.sh").chmod(0o755)

            records = [
                {
                    "name": "runner-1",
                    "path": str(runner),
                    "repo": "example/repo",
                    "enabled": True,
                }
            ]
            local = {
                "unit": "actions.runner.example.runner-1.service",
                "state": "healthy_idle",
                "boot": "disabled",
                "reason": "on_demand_inactive",
            }
            with patch.object(capacity, "observe_service", return_value=local):
                capacity.collect_local(records)

            self.assertEqual(
                records[0]["registration"],
                {"id": 42, "name": "host-runner-1"},
            )


if __name__ == "__main__":
    unittest.main()
