#!/usr/bin/env python3
"""End-to-end planner contract for resumable aggregate pressure (#107)."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_contracts import timestamp  # noqa: E402
from autoscale_planner import plan  # noqa: E402
from autoscale_runtime import MAX_PLANNER_QUEUE_ROWS, read_planner_evidence  # noqa: E402
from autoscale_store import AuditStore, Settings  # noqa: E402


class ResumedPressurePlannerContracts(unittest.TestCase):
    labels = ["self-hosted", "Linux", "local-runner"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "state"
        root.mkdir(mode=0o700)
        self.path = root / "autoscale.db"
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=20)
        self.settings = Settings(queue_gap_seconds=300)

    def at(self):
        return timestamp(self.now.isoformat())

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def job(self, job_id):
        return {
            "job_id": job_id,
            "run_id": 1000 + job_id,
            "run_attempt": 1,
            "status": "queued",
            "created_at": "2026-01-01T00:00:00Z",
            "queue_age_seconds": 7200,
            "queue_age_source": "job.created_at",
            "required_labels": list(self.labels),
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

    def snapshot(self, job_id=1, *, complete=True):
        jobs = [self.job(job_id)] if complete else []
        return {
            "schema_version": 1,
            "kind": "CapacitySnapshot",
            "observed_at": self.at(),
            "status": "complete" if complete else "inconclusive",
            "repository": {
                "requested": "example/resume",
                "nameWithOwner": "Example/Resume",
                "match_key": "example/resume",
            },
            "sources": {
                "repository": "complete",
                "queue": "complete" if complete else "inconclusive",
                "local": "complete",
                "github_runners": "complete",
            },
            "errors": [],
            "queue": {
                "status": "complete" if complete else "inconclusive",
                "observed_queued_job_count": len(jobs) if complete else None,
                "queued_job_count": len(jobs) if complete else None,
                "oldest_queued_job_id": job_id if complete else None,
                "oldest_matching_queued_job_id": job_id if complete else None,
                "jobs": jobs,
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
                "active_local_runner_count": 0,
                "observed_active_local_runner_count": 0,
                "status": "complete",
            },
        }

    def policy(self):
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
        }

    def host(self):
        return {"status": "complete", "memory_available_mib": 8192, "cpu_percent": None}

    def test_resumed_scope_keeps_proven_seconds_and_can_start_exact_idle_runner(self):
        with AuditStore(
            self.path,
            writable=True,
            clock=lambda: self.now,
            settings=self.settings,
        ) as store:
            store.observe(self.snapshot(1))
            self.advance(180)
            store.observe(self.snapshot(1))
            self.advance(180)
            store.observe(self.snapshot(1))

            # Unknown observation closes the exact job episode but only suspends
            # aggregate scope qualification.
            self.advance(30)
            store.observe(self.snapshot(complete=False))

            # A different exact job in the same capability scope resumes the
            # aggregate qualification. The 60s unknown interval is not counted.
            self.advance(30)
            current = self.snapshot(2)
            store.observe(current)
            audit = read_planner_evidence(store, "Example/Resume")

            result = plan(current, self.policy(), self.host(), audit)

        self.assertEqual(result["decision"], "START_LOCAL")
        self.assertEqual(result["action"], {"kind": "START_LOCAL", "target": "runner-idle"})
        self.assertEqual(result["requested_capacity_delta"], 1)
        self.assertIn("PRESSURE_QUALIFICATION_RESUMED", result["reason_codes"])
        self.assertIn("OBSERVED_QUEUE_THRESHOLD_MET", result["reason_codes"])

        pressure = result["evidence"]["scope"]["aggregate_sustained_pressure"][0]
        self.assertEqual(pressure["observed_queued_seconds"], 360)
        self.assertEqual(pressure["resume_count"], 1)
        self.assertEqual(pressure["last_unknown_seconds"], 60)
        self.assertEqual(len(pressure["segments"]), 2)
        self.assertEqual(pressure["segments"][0]["observed_seconds"], 360)
        self.assertEqual(pressure["segments"][1]["observed_seconds"], 0)
        self.assertEqual(pressure["current_observed_job_ids"], [2])

        # Exact evidence remains honest: current job 2 starts a fresh exact
        # episode even though aggregate pressure retains the 360 proven seconds.
        self.assertEqual([row["job_id"] for row in result["evidence"]["queue"]], [2])
        self.assertEqual(result["evidence"]["queue"][0]["first_seen_queued_at"], self.at())


    def test_historical_queue_volume_does_not_poison_current_planner_evidence(self):
        with AuditStore(
            self.path,
            writable=True,
            clock=lambda: self.now,
            settings=self.settings,
        ) as store:
            current = self.snapshot(999999)
            store.observe(current)
            repo_key = "example/resume"
            at = self.at()

            with store.transaction():
                for index in range(MAX_PLANNER_QUEUE_ROWS + 1):
                    store.connection.execute(
                        """INSERT INTO queue_observations (
                        observation_id,repo_key,run_id,run_attempt,job_id,
                        first_seen_queued_at,last_seen_queued_at,github_created_at,
                        required_labels,observation_count,max_gap_seconds,ended_at,end_reason
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            f"historical-{index}",
                            repo_key,
                            2000000 + index,
                            1,
                            3000000 + index,
                            at,
                            at,
                            "2026-01-01T00:00:00.000000+00:00",
                            json.dumps(self.labels),
                            1,
                            300,
                            at,
                            "left_queue",
                        ),
                    )

            audit = read_planner_evidence(store, "Example/Resume")

        self.assertEqual(audit["status"], "complete")
        self.assertEqual([row["job_id"] for row in audit["queue"]], [999999])



if __name__ == "__main__":
    unittest.main()
