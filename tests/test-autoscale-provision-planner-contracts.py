#!/usr/bin/env python3
"""Production-like planner contracts for #73 bounded local provisioning."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_planner import plan, policy_fingerprint  # noqa: E402


class ProvisionPlannerContracts(unittest.TestCase):
    observed_at = "2026-09-17T12:10:00+00:00"
    labels = ["self-hosted", "Linux", "X64", "runnerops"]

    def policy(self, **provision_overrides):
        provision = {
            "enabled": True,
            "max_local_runners": 4,
            "template": {
                "profile": "generic",
                "group": "runnerops",
                "labels": ["Linux", "X64", "runnerops", "self-hosted"],
                "name_prefix": "runnerops-auto",
                "runner_version": "latest",
                "runner_arch": "auto",
            },
        }
        for key, value in provision_overrides.items():
            if key.startswith("template_"):
                provision["template"][key.removeprefix("template_")] = value
            else:
                provision[key] = value
        return {
            "queue_threshold_seconds": 300,
            "max_active_local_runners": 2,
            "min_memory_available_mib": 1024,
            "max_cpu_percent": None,
            "max_burst_runners": 0,
            "cooldown_seconds": 300,
            "local_scale_out_cooldown_seconds": 30,
            "burst_enabled": False,
            "label_scope": [],
            "local_provision": provision,
        }

    def host(self):
        return {"status": "complete", "memory_available_mib": 8192, "cpu_percent": None}

    def local_runner(self, name, *, category="busy_capacity", registration_id=42, enabled=True):
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
                "name": "host-" + name,
                "status": "offline" if category == "provisioned_idle" else "online",
                "busy": False if category == "provisioned_idle" else True,
                "labels": self.labels,
            },
            "category": category,
            "reason": "fixture",
        }

    def job(self, *, status="no_matching_capacity", names=None, labels=None, job_id=101):
        names = [] if names is None else names
        labels = list(self.labels if labels is None else labels)
        return {
            "job_id": job_id,
            "run_id": 200 + job_id,
            "run_attempt": 1,
            "status": "queued",
            "created_at": "2026-09-17T11:00:00Z",
            "queue_age_seconds": 4200,
            "queue_age_source": "job.created_at",
            "required_labels": labels,
            "capacity_status": status,
            "matching_capacity": {
                "available_now": 1 if status == "available_now" else 0,
                "busy_capacity": 1 if status == "busy_capacity" else 0,
                "provisioned_idle": 1 if status == "provisioned_idle" else 0,
                "inconclusive": 1 if status == "inconclusive" else 0,
            },
            "matching_runner_ids": [42] if names else [],
            "matching_local_runner_names": names,
        }

    def snapshot(self, *, local_rows=None, active_local=1, job=None):
        job = job or self.job()
        local_rows = [self.local_runner("manual-busy")] if local_rows is None else local_rows
        counts = {
            key: sum(row.get("category") == key for row in local_rows)
            for key in ("available_now", "busy_capacity", "provisioned_idle", "inconclusive")
        }
        return {
            "schema_version": 1,
            "kind": "CapacitySnapshot",
            "observed_at": self.observed_at,
            "status": "complete",
            "repository": {
                "requested": "example/runnerops",
                "nameWithOwner": "Example/RunnerOps",
                "match_key": "example/runnerops",
            },
            "sources": {
                "repository": "complete",
                "queue": "complete",
                "local": "complete",
                "github_runners": "complete",
            },
            "errors": [],
            "queue": {
                "status": "complete",
                "observed_queued_job_count": 1,
                "queued_job_count": 1,
                "oldest_queued_job_id": job["job_id"],
                "oldest_matching_queued_job_id": job["job_id"],
                "jobs": [job],
            },
            "capacity": {
                "counts": counts,
                "runners": local_rows,
                "matching_basis": "required_labels",
                "remote_scope": "repository_runners_endpoint",
            },
            "host": {
                "active_local_runner_count": active_local,
                "observed_active_local_runner_count": active_local,
                "status": "complete",
            },
        }

    def audit(self, job=None, *, active_burst=0):
        job = job or self.job()
        return {
            "status": "complete",
            "error": None,
            "active_burst_capacity": active_burst,
            "last_scaling_action_started_at": None,
            "last_scaling_action_kind": None,
            "queue": [
                {
                    "observation_id": "observation-1",
                    "repository": "Example/RunnerOps",
                    "job_id": job["job_id"],
                    "run_id": job["run_id"],
                    "run_attempt": 1,
                    "first_seen_queued_at": "2026-09-17T12:00:00+00:00",
                    "last_seen_queued_at": self.observed_at,
                    "continuous_queued": True,
                    "required_labels": list(job["required_labels"]),
                    "github_created_at": "2026-09-17T11:00:00+00:00",
                    "observed_queued_seconds": 600,
                }
            ],
        }

    def decide(self, *, policy=None, snapshot=None, job=None, audit=None):
        job = job or self.job()
        return plan(
            snapshot or self.snapshot(job=job),
            policy or self.policy(),
            self.host(),
            audit or self.audit(job),
        )

    def test_enabled_compatible_policy_provisions_exact_lowest_free_slot(self):
        result = self.decide()
        self.assertEqual(result["decision"], "PROVISION_LOCAL")
        self.assertEqual(result["action"], {"kind": "PROVISION_LOCAL", "target": "runnerops-auto-01"})
        self.assertIn("LOCAL_POOL_BELOW_MAX", result["reason_codes"])
        scope = result["evidence"]["scope"]
        self.assertEqual(scope["current_local_pool_size"], 1)
        self.assertEqual(scope["max_local_pool_size"], 4)
        self.assertEqual(scope["provisioning_target"], "runnerops-auto-01")
        self.assertEqual(
            {value.casefold() for value in scope["selected_provisioning_scope"]},
            {value.casefold() for value in self.labels},
        )

    def test_provisioning_disabled_blocks_local_creation_when_burst_is_off(self):
        result = self.decide(policy=self.policy(enabled=False))
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIn("LOCAL_PROVISION_DISABLED", result["reason_codes"])
        self.assertIn("BURST_DISABLED", result["reason_codes"])
        self.assertIsNone(result["action"])

    def test_pool_at_max_is_independent_from_active_limit(self):
        rows = [
            self.local_runner("runnerops-auto-01", registration_id=1),
            self.local_runner("runnerops-auto-02", category="provisioned_idle", registration_id=2),
            self.local_runner("manual-disabled", registration_id=3, enabled=False),
        ]
        policy = self.policy(max_local_runners=3)
        policy["max_active_local_runners"] = 5
        result = self.decide(policy=policy, snapshot=self.snapshot(local_rows=rows, active_local=1))
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIn("LOCAL_POOL_AT_MAX", result["reason_codes"])
        self.assertEqual(result["evidence"]["scope"]["current_local_pool_size"], 3)
        self.assertEqual(result["evidence"]["scope"]["max_local_pool_size"], 3)

    def test_pool_can_grow_even_when_active_count_is_smaller_than_registered_pool(self):
        rows = [
            self.local_runner("runnerops-auto-01", registration_id=1),
            self.local_runner("manual-idle", category="provisioned_idle", registration_id=2),
            self.local_runner("manual-disabled", registration_id=3, enabled=False),
        ]
        policy = self.policy(max_local_runners=4)
        policy["max_active_local_runners"] = 5
        result = self.decide(policy=policy, snapshot=self.snapshot(local_rows=rows, active_local=1))
        self.assertEqual(result["decision"], "PROVISION_LOCAL")
        self.assertEqual(result["action"]["target"], "runnerops-auto-02")
        self.assertEqual(result["evidence"]["scope"]["current_local_pool_size"], 3)

    def test_incompatible_template_blocks_without_inventing_toolchain(self):
        policy = self.policy(template_labels=["self-hosted", "Linux", "X64", "local-runner"])
        result = self.decide(policy=policy)
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIn("LOCAL_PROVISION_TEMPLATE_INCOMPATIBLE", result["reason_codes"])
        self.assertIsNone(result["action"])

    def test_burst_is_considered_only_after_local_provisioning_is_unavailable(self):
        policy = self.policy(enabled=False)
        policy["burst_enabled"] = True
        policy["max_burst_runners"] = 1
        result = self.decide(policy=policy)
        self.assertEqual(result["decision"], "BURST_CLOUD")
        self.assertIn("LOCAL_PROVISION_DISABLED", result["reason_codes"])
        self.assertEqual(result["action"], {"kind": "BURST_CLOUD", "target": "Example/RunnerOps"})

    def test_matching_provisioned_idle_keeps_start_local_precedence(self):
        idle = self.local_runner("runner-idle", category="provisioned_idle", registration_id=77)
        job = self.job(status="provisioned_idle", names=["runner-idle"])
        result = self.decide(
            job=job,
            snapshot=self.snapshot(local_rows=[idle], active_local=1, job=job),
            audit=self.audit(job),
        )
        self.assertEqual(result["decision"], "START_LOCAL")
        self.assertEqual(result["action"]["target"], "runner-idle")

    def test_target_and_plan_are_deterministic_for_identical_evidence(self):
        first = self.decide()
        second = self.decide()
        self.assertEqual(first["action"], second["action"])
        self.assertEqual(first["decision_id"], second["decision_id"])

    def test_provision_template_changes_policy_fingerprint(self):
        first = self.policy()
        second = deepcopy(first)
        second["local_provision"]["template"]["name_prefix"] = "different"
        self.assertNotEqual(policy_fingerprint(first), policy_fingerprint(second))


if __name__ == "__main__":
    unittest.main()
