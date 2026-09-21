#!/usr/bin/env python3
"""Focused contracts for the #73 local provisioning boundary."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_provision import (  # noqa: E402
    ProvisionPolicyError,
    compatible_scopes,
    load_provision_policy,
    local_pool_size,
    provision_exact,
    provisioned_identity,
    provisioning_candidate,
    select_pool_slot,
)


class ProvisionContracts(unittest.TestCase):
    def policy(self, **overrides):
        env = {
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "true",
            "RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS": "4",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE": "python",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP": "example",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS": "self-hosted,Linux,X64,python,local-runner",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX": "example-auto",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_VERSION": "2.999.0",
            "RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_ARCH": "x64",
        }
        env.update(overrides)
        return load_provision_policy(env)

    def runner(self, name, *, category="busy_capacity", enabled=True, registration_id=100):
        return {
            "name": name,
            "registration_id": registration_id,
            "scope": "local",
            "enabled": enabled,
            "local": {
                "state": "healthy_idle" if category == "provisioned_idle" else "active",
            },
            "github": {
                "id": registration_id,
                "status": "offline" if category == "provisioned_idle" else "online",
                "busy": False if category == "provisioned_idle" else True,
            },
            "category": category,
        }

    def snapshot(self, runners=()):
        return {
            "sources": {"local": "complete"},
            "capacity": {"runners": list(runners)},
        }

    def fake_runnerctl(self, directory, *, planned=None, applied=None, apply_rc=0, marker=None):
        log = Path(directory) / "calls.log"
        script = Path(directory) / "runnerctl"
        planned = planned or "example-auto-01"
        applied = applied or planned
        marker_line = ""
        if marker == "partial":
            marker_line = 'echo "[PARTIAL] runner=$name repo=Example/Project phase=systemd-migrate" >&2\n'
        elif marker == "inconclusive":
            marker_line = 'echo "[INCONCLUSIVE] runner=$name repo=Example/Project phase=configure remote-registration=unknown" >&2\n'
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'echo "$*" >> "{log}"\n'
            'name=""\nplan=0\nargs=("$@")\n'
            'for ((i=0; i<${#args[@]}; i++)); do\n'
            '  [[ "${args[$i]}" == "--plan" ]] && plan=1\n'
            '  if [[ "${args[$i]}" == "--name" ]]; then name="${args[$((i+1))]}"; fi\n'
            'done\n'
            'if [[ "$plan" == "1" ]]; then\n'
            f'  echo "- runner name: {planned}"\n'
            '  exit 0\n'
            'fi\n'
            + marker_line
            + (f'echo "[OK] runner={applied} repo=Example/Project"\n' if apply_rc == 0 else "")
            + f"exit {apply_rc}\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script, log

    def test_disabled_policy_is_safe_and_enabled_policy_is_explicit(self):
        disabled = load_provision_policy({})
        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["max_local_runners"], 0)
        with self.assertRaises(ProvisionPolicyError):
            load_provision_policy({"RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED": "true"})
        with self.assertRaises(ProvisionPolicyError):
            self.policy(RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE="auto")
        policy = self.policy()
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["max_local_runners"], 4)
        self.assertEqual(policy["template"]["profile"], "python")
        self.assertIn("local-runner", policy["template"]["labels"])

    def test_group_and_prefix_are_normalized_for_exact_identity(self):
        policy = self.policy(
            RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP="Example-Team",
            RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX="Example-Auto",
        )
        self.assertEqual(policy["template"]["group"], "example-team")
        self.assertEqual(policy["template"]["name_prefix"], "example-auto")
        candidate = provisioning_candidate(
            self.snapshot(),
            policy,
            [["self-hosted", "Linux", "X64", "python", "local-runner"]],
        )
        self.assertEqual(candidate["target"], "example-auto-01")

    def test_pool_limit_counts_idle_and_disabled_local_records(self):
        snapshot = self.snapshot(
            [
                self.runner("example-auto-01", category="busy_capacity"),
                self.runner("manual-idle", category="provisioned_idle", registration_id=101),
                self.runner("disabled", enabled=False, registration_id=102),
            ]
        )
        self.assertEqual(local_pool_size(snapshot), 3)
        self.assertEqual(select_pool_slot(snapshot, "example-auto", 4), "example-auto-02")
        self.assertIsNone(select_pool_slot(snapshot, "example-auto", 3))

    def test_template_scope_compatibility_is_explicit_and_case_insensitive(self):
        scopes = [
            ["self-hosted", "Linux", "X64", "python", "local-runner"],
            ["self-hosted", "Linux", "GPU"],
        ]
        labels = ["SELF-HOSTED", "linux", "x64", "python", "local-runner", "extra"]
        self.assertEqual(len(compatible_scopes(scopes, labels)), 1)
        self.assertIn("python", [value.lower() for value in compatible_scopes(scopes, labels)[0]])

    def test_candidate_uses_separate_pool_limit_and_lowest_free_slot(self):
        snapshot = self.snapshot([self.runner("example-auto-01")])
        scope = [["self-hosted", "Linux", "X64", "python", "local-runner"]]
        result = provisioning_candidate(snapshot, self.policy(), scope)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["target"], "example-auto-02")
        self.assertEqual(result["current_local_pool_size"], 1)
        self.assertEqual(result["max_local_pool_size"], 4)

        at_max = provisioning_candidate(
            snapshot, self.policy(RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS="1"), scope
        )
        self.assertEqual(at_max["reason"], "LOCAL_POOL_AT_MAX")

        disabled = provisioning_candidate(snapshot, load_provision_policy({}), scope)
        self.assertEqual(disabled["reason"], "LOCAL_PROVISION_DISABLED")

    def test_incompatible_template_never_selects_target(self):
        snapshot = self.snapshot()
        result = provisioning_candidate(
            snapshot,
            self.policy(
                RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS="self-hosted,Linux,X64,local-runner"
            ),
            [["self-hosted", "Linux", "X64", "GPU"]],
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "LOCAL_PROVISION_TEMPLATE_INCOMPATIBLE")
        self.assertNotIn("target", result)

    def test_preview_collision_aborts_before_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, log = self.fake_runnerctl(tmp, planned="example-auto-02")
            result = provision_exact(
                "Example/Project", "example-auto-01", self.policy(), runnerctl=script
            )
            self.assertEqual(result["code"], "PROVISION_TARGET_COLLISION")
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 1)
            self.assertIn("--plan", calls[0])

    def test_exact_plan_apply_success_and_bounded_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, log = self.fake_runnerctl(tmp)
            result = provision_exact(
                "Example/Project", "example-auto-01", self.policy(), runnerctl=script
            )
            self.assertEqual(
                result,
                {"status": "ok", "code": "PROVISION_ADD_COMPLETED", "exit_code": 0},
            )
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 2)
            self.assertIn("--plan", calls[0])
            self.assertNotIn("--plan", calls[1])
            self.assertIn("--name example-auto-01", calls[1])

    def test_partial_and_inconclusive_results_are_not_reported_as_safe_failure(self):
        for marker, code in (
            ("partial", "PROVISION_PARTIAL"),
            ("inconclusive", "PROVISION_OUTCOME_UNKNOWN"),
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                script, _ = self.fake_runnerctl(tmp, apply_rc=1, marker=marker)
                result = provision_exact(
                    "Example/Project", "example-auto-01", self.policy(), runnerctl=script
                )
                self.assertEqual(result["status"], "inconclusive")
                self.assertEqual(result["code"], code)

    def test_unmarked_nonzero_apply_is_inconclusive_after_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, _ = self.fake_runnerctl(tmp, apply_rc=17)
            result = provision_exact(
                "Example/Project", "example-auto-01", self.policy(), runnerctl=script
            )
            self.assertEqual(result["status"], "inconclusive")
            self.assertEqual(result["code"], "PROVISION_OUTCOME_UNKNOWN")
            self.assertEqual(result["exit_code"], 17)

    def test_post_apply_name_mismatch_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, _ = self.fake_runnerctl(
                tmp, planned="example-auto-01", applied="example-auto-01-2"
            )
            result = provision_exact(
                "Example/Project", "example-auto-01", self.policy(), runnerctl=script
            )
            self.assertEqual(result["status"], "inconclusive")
            self.assertEqual(result["code"], "PROVISION_TARGET_MISMATCH")

    def test_positive_identity_requires_exact_healthy_provisioned_idle(self):
        good = self.snapshot(
            [
                self.runner(
                    "example-auto-01",
                    category="provisioned_idle",
                    registration_id=42,
                )
            ]
        )
        self.assertEqual(provisioned_identity(good, "example-auto-01"), "42")
        busy = self.snapshot(
            [self.runner("example-auto-01", category="busy_capacity", registration_id=42)]
        )
        self.assertIsNone(provisioned_identity(busy, "example-auto-01"))
        self.assertIsNone(provisioned_identity(good, "different"))

    def test_exact_name_boundary_subsuite(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tests/test-autoscale-exact-provisioning-contracts.py")],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
