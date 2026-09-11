#!/usr/bin/env python3
"""Focused contracts for the read-only deterministic autoscale planner."""

import json
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_planner import PolicyError, load_policy, plan  # noqa: E402


class PlannerContracts(unittest.TestCase):
    observed_at = "2026-09-10T12:00:00+00:00"

    def policy(self, **overrides):
        value = {
            "queue_threshold_seconds": 300,
            "max_active_local_runners": 2,
            "min_memory_available_mib": 1024,
            "max_cpu_percent": None,
            "max_burst_runners": 1,
            "cooldown_seconds": 300,
            "burst_enabled": True,
            "label_scope": [],
        }
        value.update(overrides)
        return value

    def host(self, **overrides):
        value = {"status": "complete", "memory_available_mib": 8192, "cpu_percent": None}
        value.update(overrides)
        return value

    def job(self, status="busy_capacity", names=None, labels=None):
        if names is None:
            names = ["runner-busy"] if status in ("busy_capacity", "provisioned_idle", "available_now") else []
        if labels is None:
            labels = ["self-hosted", "Linux", "X64", "runnerops"]
        matching = {
            "available_now": 1 if status == "available_now" else 0,
            "busy_capacity": 1 if status == "busy_capacity" else 0,
            "provisioned_idle": 1 if status == "provisioned_idle" else 0,
            "inconclusive": 1 if status == "inconclusive" else 0,
        }
        return {
            "job_id": 101,
            "run_id": 201,
            "run_attempt": 1,
            "status": "queued",
            "created_at": "2026-09-10T10:00:00Z",
            "queue_age_seconds": 7200,
            "queue_age_source": "job.created_at",
            "required_labels": labels,
            "capacity_status": status,
            "matching_capacity": matching,
            "matching_runner_ids": [1] if names else [],
            "matching_local_runner_names": names,
        }

    def runner(self, name, category):
        return {
            "name": name,
            "registration_id": 1,
            "scope": "local",
            "enabled": True,
            "local": {"state": "active" if category != "provisioned_idle" else "healthy_idle"},
            "github": {
                "id": 1,
                "name": "host-" + name,
                "status": "online" if category != "provisioned_idle" else "offline",
                "busy": category == "busy_capacity",
                "labels": ["self-hosted", "Linux", "X64", "runnerops"],
            },
            "category": category,
            "reason": "fixture",
        }

    def snapshot(self, status="busy_capacity", active_local=1, names=None, labels=None):
        job = self.job(status=status, names=names, labels=labels)
        runners = []
        for name in job["matching_local_runner_names"]:
            category = status if status in ("available_now", "busy_capacity", "provisioned_idle") else "busy_capacity"
            runners.append(self.runner(name, category))
        counts = {
            "available_now": sum(r["category"] == "available_now" for r in runners),
            "busy_capacity": sum(r["category"] == "busy_capacity" for r in runners),
            "provisioned_idle": sum(r["category"] == "provisioned_idle" for r in runners),
            "inconclusive": sum(r["category"] == "inconclusive" for r in runners),
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
                "oldest_queued_job_id": 101,
                "oldest_matching_queued_job_id": 101,
                "jobs": [job],
            },
            "capacity": {
                "counts": counts,
                "runners": runners,
                "matching_basis": "required_labels",
                "remote_scope": "repository_runners_endpoint",
            },
            "host": {
                "active_local_runner_count": active_local,
                "observed_active_local_runner_count": active_local,
                "status": "complete",
            },
        }

    def audit(self, seconds=600, active_burst=0, last_started=None):
        first = "2026-09-10T11:50:00+00:00"
        if seconds == 120:
            first = "2026-09-10T11:58:00+00:00"
        elif seconds == 300:
            first = "2026-09-10T11:55:00+00:00"
        return {
            "status": "complete",
            "error": None,
            "active_burst_capacity": active_burst,
            "last_scaling_action_started_at": last_started,
            "queue": [
                {
                    "observation_id": "observation-1",
                    "repository": "Example/RunnerOps",
                    "job_id": 101,
                    "run_id": 201,
                    "run_attempt": 1,
                    "first_seen_queued_at": first,
                    "last_seen_queued_at": self.observed_at,
                    "continuous_queued": True,
                    "required_labels": ["Linux", "X64", "runnerops", "self-hosted"],
                    "github_created_at": "2026-09-10T10:00:00+00:00",
                    "observed_queued_seconds": seconds,
                }
            ],
        }

    def decision(self, snapshot=None, policy=None, host=None, audit=None):
        return plan(
            snapshot or self.snapshot(),
            policy or self.policy(),
            host or self.host(),
            audit or self.audit(),
        )

    def test_queue_below_observed_threshold_waits_and_ignores_github_created_age(self):
        result = self.decision(audit=self.audit(seconds=120))
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["reason_codes"], ["QUEUE_BELOW_THRESHOLD"])
        self.assertEqual(result["requested_capacity_delta"], 0)
        self.assertIsNone(result["action"])
        self.assertEqual(result["evidence"]["scope"]["oldest_observed_queued_seconds"], 120)

    def test_matching_healthy_on_demand_idle_runner_starts_exactly_one(self):
        snapshot = self.snapshot(status="provisioned_idle", names=["runner-z", "runner-a"])
        snapshot["capacity"]["runners"][0]["registration_id"] = 2
        snapshot["capacity"]["runners"][1]["registration_id"] = 1
        result = self.decision(snapshot=snapshot)
        self.assertEqual(result["decision"], "START_LOCAL")
        self.assertEqual(result["action"], {"kind": "START_LOCAL", "target": "runner-a"})
        self.assertIn("MATCHING_LOCAL_RUNNER_IDLE", result["reason_codes"])
        self.assertIn("OBSERVED_QUEUE_THRESHOLD_MET", result["reason_codes"])

    def test_busy_capacity_safe_headroom_and_local_slot_provisions_local(self):
        result = self.decision(snapshot=self.snapshot(status="busy_capacity", active_local=1))
        self.assertEqual(result["decision"], "PROVISION_LOCAL")
        self.assertEqual(result["requested_capacity_delta"], 1)
        self.assertEqual(result["action"]["kind"], "PROVISION_LOCAL")
        self.assertIn("LOCAL_POOL_BELOW_MAX", result["reason_codes"])

    def test_saturated_local_pool_and_allowed_burst_plans_cloud(self):
        result = self.decision(
            snapshot=self.snapshot(status="busy_capacity", active_local=1),
            policy=self.policy(max_active_local_runners=1, max_burst_runners=2, burst_enabled=True),
            audit=self.audit(active_burst=1),
        )
        self.assertEqual(result["decision"], "BURST_CLOUD")
        self.assertEqual(result["action"]["kind"], "BURST_CLOUD")
        self.assertIn("LOCAL_CAPACITY_SATURATED", result["reason_codes"])
        self.assertIn("LOCAL_POOL_AT_MAX", result["reason_codes"])

    def test_memory_and_cpu_guards_hold(self):
        memory = self.decision(host=self.host(memory_available_mib=512))
        self.assertEqual(memory["decision"], "HOLD")
        self.assertIn("HOST_MEMORY_HEADROOM_LOW", memory["reason_codes"])

        cpu = self.decision(
            policy=self.policy(max_cpu_percent=75.0),
            host=self.host(cpu_percent=90.0),
        )
        self.assertEqual(cpu["decision"], "HOLD")
        self.assertIn("HOST_CPU_THRESHOLD_EXCEEDED", cpu["reason_codes"])

    def test_burst_limit_holds(self):
        result = self.decision(
            snapshot=self.snapshot(active_local=1),
            policy=self.policy(max_active_local_runners=1, max_burst_runners=1),
            audit=self.audit(active_burst=1),
        )
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("BURST_LIMIT_REACHED", result["reason_codes"])

    def test_missing_api_lifecycle_audit_or_host_evidence_is_inconclusive(self):
        cases = []
        api = self.snapshot()
        api["sources"]["github_runners"] = "inconclusive"
        cases.append((api, self.host(), self.audit()))

        lifecycle = self.snapshot()
        lifecycle["queue"]["jobs"][0]["capacity_status"] = "inconclusive"
        cases.append((lifecycle, self.host(), self.audit()))

        cases.append((self.snapshot(), self.host(), {"status": "missing", "error": "store_missing", "queue": [], "active_burst_capacity": None, "last_scaling_action_started_at": None}))
        cases.append((self.snapshot(), self.host(status="inconclusive", memory_available_mib=None), self.audit()))

        for snapshot, host, audit in cases:
            with self.subTest(snapshot=snapshot["sources"], host=host, audit=audit["status"]):
                result = self.decision(snapshot=snapshot, host=host, audit=audit)
                self.assertEqual(result["decision"], "INCONCLUSIVE")
                self.assertEqual(result["status"], "inconclusive")
                self.assertEqual(result["reason_codes"], ["EVIDENCE_INCONCLUSIVE"])

    def test_burst_disabled_after_local_exhaustion_is_blocked(self):
        result = self.decision(
            snapshot=self.snapshot(active_local=1),
            policy=self.policy(max_active_local_runners=1, burst_enabled=False),
        )
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIn("BURST_DISABLED", result["reason_codes"])
        self.assertIn("LOCAL_CAPACITY_SATURATED", result["reason_codes"])

    def test_available_capacity_waits_without_requiring_audit_history(self):
        result = self.decision(
            snapshot=self.snapshot(status="available_now"),
            audit={"status": "missing", "error": "store_missing", "queue": [], "active_burst_capacity": None, "last_scaling_action_started_at": None},
        )
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["reason_codes"], ["MATCHING_LOCAL_RUNNER_AVAILABLE"])

    def test_available_capacity_for_one_job_does_not_hide_pressure_for_another(self):
        snapshot = self.snapshot(status="available_now", names=["runner-ready"])
        pressure = self.job(
            status="no_matching_capacity",
            names=[],
            labels=["self-hosted", "Linux", "X64", "gpu"],
        )
        pressure["job_id"] = 102
        pressure["run_id"] = 202
        snapshot["queue"]["jobs"].append(pressure)
        snapshot["queue"]["queued_job_count"] = 2
        snapshot["queue"]["observed_queued_job_count"] = 2

        audit = self.audit()
        audit["queue"] = [
            {
                "observation_id": "observation-2",
                "repository": "Example/RunnerOps",
                "job_id": 102,
                "run_id": 202,
                "run_attempt": 1,
                "first_seen_queued_at": "2026-09-10T11:50:00+00:00",
                "last_seen_queued_at": self.observed_at,
                "continuous_queued": True,
                "required_labels": ["Linux", "X64", "gpu", "self-hosted"],
                "github_created_at": "2026-09-10T10:00:00+00:00",
            }
        ]

        result = self.decision(snapshot=snapshot, audit=audit)
        self.assertEqual(result["decision"], "PROVISION_LOCAL")
        self.assertEqual(result["evidence"]["scope"]["scoped_queued_job_count"], 2)
        self.assertEqual(result["evidence"]["scope"]["pressure_queued_job_count"], 1)
        self.assertEqual(result["evidence"]["scope"]["pressure_job_ids"], [102])

    def test_label_scope_can_explicitly_block_unselected_self_hosted_work(self):
        result = self.decision(policy=self.policy(label_scope=["gpu"]))
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertEqual(result["reason_codes"], ["LABEL_SCOPE_BLOCKED"])

    def test_cooldown_holds_after_recent_scaling_action(self):
        result = self.decision(audit=self.audit(last_started="2026-09-10T11:58:00+00:00"))
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("COOLDOWN_ACTIVE", result["reason_codes"])
        self.assertEqual(result["evidence"]["audit"]["cooldown_elapsed_seconds"], 120)

    def test_identical_evidence_and_policy_produces_identical_plan_and_json(self):
        snapshot = self.snapshot()
        policy = self.policy()
        host = self.host()
        audit = self.audit()
        first = plan(deepcopy(snapshot), deepcopy(policy), deepcopy(host), deepcopy(audit))
        second = plan(deepcopy(snapshot), deepcopy(policy), deepcopy(host), deepcopy(audit))
        self.assertEqual(first, second)
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(first["reason_codes"], second["reason_codes"])
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        for reason in first["reason_codes"]:
            self.assertRegex(reason, r"^[A-Z][A-Z0-9_]*$")

    def test_policy_defaults_and_validation_are_stable(self):
        clean = {key: value for key, value in os.environ.items() if not key.startswith("RUNNER_AUTOSCALE_")}
        with patch.dict(os.environ, clean, clear=True):
            policy = load_policy()
        self.assertEqual(policy["queue_threshold_seconds"], 300)
        self.assertEqual(policy["max_active_local_runners"], 1)
        self.assertEqual(policy["min_memory_available_mib"], 1024)
        self.assertIsNone(policy["max_cpu_percent"])
        self.assertEqual(policy["max_burst_runners"], 0)
        self.assertEqual(policy["cooldown_seconds"], 300)
        self.assertFalse(policy["burst_enabled"])
        self.assertEqual(policy["label_scope"], [])

        with patch.dict(os.environ, {"RUNNER_AUTOSCALE_BURST_ENABLED": "maybe"}, clear=False):
            with self.assertRaises(PolicyError):
                load_policy()


if __name__ == "__main__":
    unittest.main()
