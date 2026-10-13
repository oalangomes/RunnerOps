import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from runnerops.workflows.intelligence import analyze, read_evidence


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "operational.json"
        self.payload = {
            "schema_version": 1, "kind": "OperationalEvidence",
            "repository": {"nameWithOwner": "example/ci"},
            "period": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"},
            "ci": {"queued_jobs_observed": 2, "queue_age_seconds": {"count": 2}},
            "autoscale": {"decisions": {"by_kind": {"START_LOCAL": 1}},
                          "actions": {"by_kind": {"START_LOCAL": 1},
                                      "by_state": {"failed": 1}}},
            "incomplete_evidence": [{"source": "ci", "reason": "ci_history_not_persisted"}],
        }
        self.file.write_text(json.dumps(self.payload), encoding="utf-8")

    def test_observed_and_unknown_remain_distinct(self):
        value = analyze(read_evidence(self.file))
        self.assertEqual(value["status"], "inconclusive")
        self.assertEqual(value["model_used"], False)
        self.assertEqual([v["class"] for v in value["signals"]],
                         ["OBSERVED", "OBSERVED", "UNKNOWN"])
        self.assertEqual(value["signals"][0]["value"], 2)
        self.assertIn("ci_history_not_persisted", value["incomplete_evidence"])
        self.assertFalse(value["mutation"])

    def test_fail_closed_invalid_kind_and_symlink(self):
        self.payload["kind"] = "different"
        self.file.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_evidence(self.file)
        link = Path(self.temp.name) / "sym.json"
        link.symlink_to(self.file)
        with self.assertRaises(ValueError):
            read_evidence(link)

    def test_cli_offline_replay_is_bounded(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        command = [sys.executable, "-B", "-m", "runnerops.workflows.cli",
                   "intelligence", "--evidence", str(self.file), "--json"]
        proc = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["kind"], "WorkflowIntelligence")

    def test_model_requires_explicit_provider(self):
        from runnerops.workflows.cli import main
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["intelligence", "--evidence", str(self.file),
                                   "--model", "demo", "--json"]), 2)


if __name__ == "__main__":
    unittest.main()
