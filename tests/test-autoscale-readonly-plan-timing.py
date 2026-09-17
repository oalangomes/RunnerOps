#!/usr/bin/env python3
"""Contracts for bounded read-only autoscale evidence projection (#108)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_planner import _aggregate_queue_evidence, plan  # noqa: E402


class ReadOnlyPlanTimingContracts(unittest.TestCase):
    snapshot_at = "2026-09-10T12:00:00+00:00"
    persisted_at = "2026-09-10T11:59:30+00:00"
    labels = ["self-hosted", "Linux", "local-runner"]

    def policy(self, threshold=300):
        return {
            "queue_threshold_seconds": threshold,
            "max_active_local_runners": 2,
            "min_memory_available_mib": 1024,
            "max_cpu_percent": None,
            "max_burst_runners": 0,
            "cooldown_seconds": 300,
            "local_scale_out_cooldown_seconds": 30,
            "burst_enabled": False,
            "label_scope": [],
        }

    def host(self):
        return {
            "status": "complete",
            "memory_available_mib": 8192,
            "cpu_percent": None,
        }

    def job(self, *, job_id=101, labels=None):
        return {
            "job_id": job_id,
            "run_id": 201,
            "run_attempt": 1,
            "status": "queued",
            "created_at": "2026-09-10T10:00:00Z",
            "queue_age_seconds": 7200,
            "queue_age_source": "job.created_at",
            "required_labels": labels or list(self.labels),
            "capacity_status": "provisioned_idle",
            "matching_capacity": {
                "available_now": 0,
                "busy_capacity": 0,
                "provisioned_idle": 1,
                "inconclusive": 0,
            },
            "matching_runner_ids": [1],
            "matching_local_runner_names": ["runner-idle"],
        }

    def snapshot(self, *, job=None):
        job = job or self.job()
        return {
            "schema_version": 1,
            "kind": "CapacitySnapshot",
            "observed_at": self.snapshot_at,
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
                "counts": {
                    "available_now": 0,
                    "busy_capacity": 0,
                    "provisioned_idle": 1,
                    "inconclusive": 0,
                },
                "runners": [
                    {
                        "name": "runner-idle",
                        "registration_id": 1,
                        "scope": "local",
                        "enabled": True,
                        "local": {"state": "healthy_idle"},
                        "github": {
                            "id": 1,
                            "name": "host-runner-idle",
                            "status": "offline",
                            "busy": False,
                            "labels": list(self.labels),
                        },
                        "category": "provisioned_idle",
                        "reason": "on_demand_inactive",
                    }
                ],
                "matching_basis": "required_labels",
                "remote_scope": "repository_runners_endpoint",
            },
            "host": {
                "active_local_runner_count": 1,
                "observed_active_local_runner_count": 1,
                "status": "complete",
            },
        }

    def audit(self, *, first="2026-09-10T11:50:00+00:00", last=None, max_gap=300):
        return {
            "status": "complete",
            "error": None,
            "active_burst_capacity": 0,
            "last_scaling_action_started_at": None,
            "last_scaling_action_kind": None,
            "queue": [
                {
                    "observation_id": "observation-1",
                    "repository": "Example/RunnerOps",
                    "job_id": 101,
                    "run_id": 201,
                    "run_attempt": 1,
                    "first_seen_queued_at": first,
                    "last_seen_queued_at": last or self.persisted_at,
                    "continuous_queued": True,
                    "required_labels": list(self.labels),
                    "github_created_at": "2026-09-10T10:00:00+00:00",
                    "max_gap_seconds": max_gap,
                }
            ],
        }

    def test_bounded_lag_uses_only_persisted_duration(self):
        aggregate = _aggregate_queue_evidence(
            [self.job()], self.audit(), self.snapshot_at
        )
        self.assertEqual(len(aggregate), 1)
        row = aggregate[0]
        self.assertEqual(row["last_seen_queued_at"], "2026-09-10T11:59:30.000000+00:00")
        self.assertEqual(row["current_observed_at"], self.snapshot_at)
        self.assertEqual(row["read_only_projection_lag_seconds"], 30)
        # 11:50 -> 11:59:30 is 570s. The 30s unknown lag is not counted.
        self.assertEqual(row["observed_queued_seconds"], 570)
        self.assertEqual(row["current_observed_job_ids"], [101])

    def test_plan_can_start_from_fresh_snapshot_and_bounded_persisted_evidence(self):
        result = plan(self.snapshot(), self.policy(), self.host(), self.audit())
        self.assertEqual(result["decision"], "START_LOCAL")
        self.assertEqual(result["action"], {"kind": "START_LOCAL", "target": "runner-idle"})
        self.assertEqual(result["requested_capacity_delta"], 1)
        evidence = result["evidence"]
        self.assertEqual(evidence["audit"]["read_only_evidence_lag_seconds"], 30)
        self.assertEqual(
            evidence["audit"]["persisted_queue_observed_at"],
            "2026-09-10T11:59:30.000000+00:00",
        )
        self.assertEqual(
            evidence["audit"]["snapshot_observed_at"],
            "2026-09-10T12:00:00.000000+00:00",
        )
        self.assertEqual(
            evidence["scope"]["aggregate_sustained_pressure"][0]["observed_queued_seconds"],
            570,
        )

    def test_unknown_lag_never_pushes_pressure_over_threshold(self):
        # Proven time is 570s. Counting the unknown 30s would incorrectly reach 600s.
        result = plan(self.snapshot(), self.policy(threshold=600), self.host(), self.audit())
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["reason_codes"], ["QUEUE_BELOW_THRESHOLD"])
        self.assertEqual(result["evidence"]["scope"]["oldest_observed_queued_seconds"], 570)

    def test_projection_beyond_retained_gap_fails_closed(self):
        audit = self.audit(last="2026-09-10T11:54:00+00:00", max_gap=300)
        aggregate = _aggregate_queue_evidence([self.job()], audit, self.snapshot_at)
        self.assertEqual(aggregate, [])
        result = plan(self.snapshot(), self.policy(), self.host(), audit)
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertEqual(result["reason_codes"], ["QUEUE_EVIDENCE_NOT_CURRENT"])

    def test_disappeared_exact_identity_cannot_be_projected(self):
        current = self.job(job_id=102)
        aggregate = _aggregate_queue_evidence([current], self.audit(), self.snapshot_at)
        self.assertEqual(aggregate, [])
        result = plan(self.snapshot(job=current), self.policy(), self.host(), self.audit())
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertEqual(result["reason_codes"], ["QUEUE_EVIDENCE_NOT_CURRENT"])

    def test_label_change_cannot_borrow_persisted_scope(self):
        current = self.job(labels=["self-hosted", "Linux", "GPU"])
        aggregate = _aggregate_queue_evidence([current], self.audit(), self.snapshot_at)
        self.assertEqual(aggregate, [])
        result = plan(self.snapshot(job=current), self.policy(), self.host(), self.audit())
        # Exact evidence detects the identity/label contradiction before aggregate projection.
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertEqual(result["reason_codes"], ["EVIDENCE_INCONCLUSIVE"])

    def test_exact_timestamp_path_remains_zero_lag(self):
        audit = self.audit(last=self.snapshot_at)
        result = plan(self.snapshot(), self.policy(), self.host(), audit)
        self.assertEqual(result["decision"], "START_LOCAL")
        self.assertEqual(result["evidence"]["audit"]["read_only_evidence_lag_seconds"], 0)
        self.assertEqual(
            result["evidence"]["scope"]["aggregate_sustained_pressure"][0]["last_seen_queued_at"],
            "2026-09-10T12:00:00.000000+00:00",
        )

    def test_incomplete_snapshot_remains_fail_closed(self):
        snapshot = self.snapshot()
        snapshot["sources"]["queue"] = "inconclusive"
        snapshot["queue"]["status"] = "inconclusive"
        snapshot["queue"]["queued_job_count"] = None
        result = plan(snapshot, self.policy(), self.host(), self.audit())
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertEqual(result["reason_codes"], ["EVIDENCE_INCONCLUSIVE"])


if __name__ == "__main__":
    unittest.main()
