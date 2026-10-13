import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from runnerops.workflows.adoption import apply, plan


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / ".github" / "workflows"
        self.directory.mkdir(parents=True)

    def write(self, event="push", runson="ubuntu-latest"):
        self.workflow = self.directory / "ci.yml"
        self.workflow.write_text(
            "name: Test\non: " + event + "\n"
            "jobs:\n  build:\n    runs-on: " + runson + "\n"
            "    steps:\n      - run: echo stable\n", encoding="utf-8")
        return self.workflow

    def test_plan_no_mutation(self):
        p = self.write()
        before = p.read_bytes()
        result = plan(self.root, "local-first")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["mutation"])
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(p.read_bytes(), before)

    def test_apply_explicit_and_idempotent(self):
        p = self.write()
        intended = plan(self.root, "local-first")
        with self.assertRaises(ValueError):
            apply(self.root, "local-first", intended["fingerprint"])
        done = apply(self.root, "local-first", intended["fingerprint"], confirmed=True)
        self.assertEqual(done["status"], "applied")
        self.assertIn("runs-on: [self-hosted, Linux, X64, local-runner]", p.read_text())
        self.assertIn("echo stable", p.read_text())
        follow = plan(self.root, "local-first")
        self.assertEqual(follow["status"], "ready")
        self.assertEqual(follow["changes"], [])

    def test_stale_fingerprint_rejected(self):
        p = self.write()
        intended = plan(self.root, "local-first")
        p.write_text(p.read_text() + "\n# change after approval\n")
        with self.assertRaisesRegex(ValueError, "STALE_ADOPTION_FINGERPRINT"):
            apply(self.root, "local-first", intended["fingerprint"], confirmed=True)

    def test_untrusted_pr_cannot_route_local(self):
        p = self.write(event="pull_request")
        result = plan(self.root, "local-first")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked"][0]["code"], "UNTRUSTED_OR_UNSUPPORTED_TRIGGERS")
        with self.assertRaisesRegex(ValueError, "ADOPTION_BLOCKED"):
            apply(self.root, "local-first", result["fingerprint"], confirmed=True)
        self.assertIn("runs-on: ubuntu-latest", p.read_text())

    def test_hosted_migration(self):
        p = self.write(runson="[self-hosted, Linux, X64, local-runner]")
        result = plan(self.root, "hosted")
        done = apply(self.root, "hosted", result["fingerprint"], confirmed=True)
        self.assertEqual(done["status"], "applied")
        self.assertIn("runs-on: ubuntu-latest", p.read_text())

    def test_dynamic_no_mutation(self):
        p = self.write(runson="$" + "{{ matrix.os }}")
        result = plan(self.root, "local-first")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["changes"], [])
        self.assertIn("matrix.os", p.read_text())

    def test_single_file_diff_is_byte_local(self):
        p = self.write()
        before = p.read_text()
        expected = before.replace("runs-on: ubuntu-latest",
                                  "runs-on: [self-hosted, Linux, X64, local-runner]")
        intent = plan(self.root, "local-first")
        apply(self.root, "local-first", intent["fingerprint"], confirmed=True)
        self.assertEqual(p.read_text(), expected)


if __name__ == "__main__":
    unittest.main()

