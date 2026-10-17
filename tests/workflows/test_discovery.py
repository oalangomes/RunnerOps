import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from runnerops.workflows.discovery import discover, digest


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / ".github" / "workflows"
        self.directory.mkdir(parents=True)

    def write(self, text, name="ci.yml"):
        p = self.directory / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_static_hosted_and_self_hosted(self):
        self.write("""name: Sample
on:
  push:
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
  run:
    needs: [build]
    runs-on: [self-hosted, Linux, local-runner]
""")
        actual = discover(self.root)
        self.assertEqual(actual["status"], "complete")
        w = actual["workflows"][0]
        self.assertEqual(w["triggers"], ["push", "workflow_dispatch"])
        self.assertEqual(w["jobs"][0]["runner_kind"], "hosted")
        self.assertEqual(w["jobs"][1]["runner_kind"], "self-hosted")
        self.assertEqual(w["jobs"][1]["needs"], ["build"])
        self.assertEqual(w["jobs"][1]["line"], 10)
        self.assertEqual(actual["fingerprint"], discover(self.root)["fingerprint"])

    def test_untrusted_event_on_self_hosted_is_inconclusive(self):
        self.write("on: [pull_request]\njobs:\n  ci:\n    runs-on: [self-hosted, linux]\n")
        value = discover(self.root)
        self.assertEqual(value["status"], "inconclusive")
        self.assertIn("UNTRUSTED_EVENT_SELF_HOSTED",
                      [d["code"] for d in value["diagnostics"]])

    def test_dynamic_runner_stays_unknown(self):
        dynamic = "on: push\njobs:\n  test:\n    runs-on: " + "$" + "{{ matrix.runner }}\n"
        self.write(dynamic)
        actual = discover(self.root)
        self.assertEqual(actual["status"], "inconclusive")
        self.assertEqual(actual["workflows"][0]["jobs"][0]["runner_kind"], "unknown")
        self.assertIn("DYNAMIC_OR_UNSUPPORTED_RUNS_ON",
                      [d["code"] for d in actual["diagnostics"]])

    def test_bounded_symlinks_not_followed(self):
        outside = self.root / "outside"
        outside.write_text("on: push", encoding="utf-8")
        (self.directory / "linked.yml").symlink_to(outside)
        value = discover(self.root)
        self.assertEqual(value["status"], "inconclusive")
        self.assertIsNone(value["workflows"][0]["sha256"])

    def test_missing_and_too_many(self):
        self.assertEqual(discover(self.root)["status"], "inconclusive")
        self.assertEqual(digest(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_single_file_and_fingerprint_change(self):
        p = self.write("on: push\njobs:\n  ci:\n    runs-on: ubuntu-latest\n")
        self.assertEqual(discover(p)["status"], "complete")
        fingerprint = discover(self.root)["fingerprint"]
        p.write_text(p.read_text() + "# another comment\n", encoding="utf-8")
        self.assertNotEqual(fingerprint, discover(self.root)["fingerprint"])


if __name__ == "__main__":
    unittest.main()
