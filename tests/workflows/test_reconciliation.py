import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from runnerops.workflows.reconciliation import reconcile, read_policy


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dir = self.root / ".github" / "workflows"
        self.dir.mkdir(parents=True)
        (self.dir / "ci.yml").write_text(
            "on: push\njobs:\n  ci:\n    runs-on: [self-hosted, linux, local-runner]\n",
            encoding="utf-8")

    def policy(self, scope, offered):
        path = self.root / "policy.json"
        path.write_text(json.dumps({
            "schema_version": 1, "repository": "example/ci",
            "fingerprint": "sha256:test",
            "policy": {"label_scope": scope,
                       "local_provision": {"enabled": True, "labels": offered},
                       "local_ephemeral": {"enabled": False, "labels": []}}
        }), encoding="utf-8")
        return path

    def test_unknown_when_policy_unavailable(self):
        actual = reconcile(self.root)
        self.assertEqual(actual["status"], "inconclusive")
        self.assertEqual(actual["jobs"][0]["reason"], "POLICY_EVIDENCE_MISSING")
        self.assertFalse(actual["mutation"])

    def test_compatible_labels_case_insensitive(self):
        p = self.policy(["SELF-HOSTED", "LINUX"],
                        ["self-hosted", "linux", "LOCAL-RUNNER", "x64"])
        actual = reconcile(self.root, p, repository="example/ci")
        self.assertEqual(actual["status"], "complete")
        self.assertEqual(actual["jobs"][0]["status"], "compatible")
        self.assertEqual(actual["jobs"][0]["policy_paths"], ["local_provision"])

    def test_scope_mismatch_blocks(self):
        p = self.policy(["self-hosted", "GPU"], ["self-hosted", "linux", "local-runner"])
        actual = reconcile(self.root, p, repository="example/ci")
        self.assertEqual(actual["status"], "blocked")
        self.assertEqual(actual["jobs"][0]["reason"], "LABEL_SCOPE_BLOCKED")

    def test_no_enabled_template_does_not_assert_absent_capacity(self):
        p = self.policy(["self-hosted"], ["linux"])
        actual = reconcile(self.root, p, repository="example/ci")
        self.assertEqual(actual["status"], "inconclusive")
        self.assertEqual(actual["jobs"][0]["reason"], "NO_VERIFIED_PROVISIONING_PATH")

    def test_real_autoscale_plan_projection_is_not_false_enabled(self):
        p = self.root / "autoscale-plan.json"
        p.write_text(json.dumps({
            "schema_version": 1, "kind": "AutoscalePlan",
            "repository": "example/ci", "policy_fingerprint": "sha256:real",
            "evidence": {
                "scope": {"labels": ["self-hosted", "linux"],
                          "provisioning_template_labels": ["self-hosted", "linux", "local-runner"]},
                "ephemeral": {"template_labels": ["self-hosted", "linux", "local-runner"]}
            }
        }), encoding="utf-8")
        result = reconcile(self.root, p, repository="example/ci")
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["policy_source"], "operator_supplied_autoscale_plan")
        self.assertEqual(result["jobs"][0]["template_alignment"], ["local_provision", "local_ephemeral"])

    def test_unverified_or_mismatched_repository(self):
        p = self.policy(["self-hosted"], ["self-hosted", "linux", "local-runner"])
        self.assertEqual(reconcile(self.root, p)["jobs"][0]["reason"], "POLICY_REPOSITORY_NOT_VERIFIED")
        with self.assertRaisesRegex(ValueError, "POLICY_REPOSITORY_MISMATCH"):
            reconcile(self.root, p, repository="another/repo")

    def test_missing_or_invalid_evidence_fails(self):
        p = self.policy([], [])
        data = json.loads(p.read_text())
        data["policy"]["label_scope"] = "self-hosted"
        p.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_policy(p)


if __name__ == "__main__":
    unittest.main()
