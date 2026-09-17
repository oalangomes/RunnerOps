#!/usr/bin/env python3
"""End-to-end contracts for governed PROVISION_LOCAL through run_once."""

import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_controller import run_once  # noqa: E402
from autoscale_store import AuditStore, Settings  # noqa: E402


@contextmanager
def no_lock():
    yield


class ProvisionRunOnceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state" / "autoscale.db"
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.provision_calls = []
        self.start_calls = []

    def store_factory(self):
        return AuditStore(
            self.db,
            writable=True,
            clock=lambda: self.now,
            settings=Settings(queue_gap_seconds=300),
        )

    def policy(self, **overrides):
        value = {
            "queue_threshold_seconds": 300,
            "max_active_local_runners": 4,
            "min_memory_available_mib": 1024,
            "max_cpu_percent": None,
            "max_burst_runners": 0,
            "cooldown_seconds": 300,
            "local_scale_out_cooldown_seconds": 30,
            "burst_enabled": False,
            "label_scope": [],
            "local_provision": {
                "enabled": True,
                "max_local_runners": 4,
                "template": {
                    "profile": "python",
                    "group": "runnerops",
                    "labels": ["Linux", "X64", "runnerops", "self-hosted"],
                    "name_prefix": "runnerops-auto",
                    "runner_version": "latest",
                    "runner_arch": "auto",
                },
            },
        }
        value.update(overrides)
        return value

    def runner(self, name, category="busy_capacity", registration_id=1001):
        online = category in ("available_now", "busy_capacity")
        return {
            "name": name,
            "registration_id": registration_id,
            "scope": "local",
            "enabled": True,
            "local": {
                "unit": f"actions.runner.example.{name}.service",
                "state": "active" if online else "healthy_idle",
                "boot": "disabled",
                "reason": "fixture",
            },
            "github": {
                "id": registration_id,
                "name": "host-" + name,
                "status": "online" if online else "offline",
                "busy": category == "busy_capacity",
                "labels": ["self-hosted", "Linux", "X64", "runnerops"],
            },
            "category": category,
            "reason": "fixture",
        }

    def job(self, index=0, *, matching_names=None, status="no_matching_capacity"):
        matching_names = list(matching_names or [])
        counts = {
            "available_now": 0,
            "busy_capacity": 0,
            "provisioned_idle": len(matching_names) if status == "provisioned_idle" else 0,
            "inconclusive": 0,
        }
        return {
            "job_id": 101 + index,
            "run_id": 201 + index,
            "run_attempt": 1,
            "status": "queued",
            "created_at": (self.now - timedelta(hours=1)).isoformat(),
            "queue_age_seconds": 3600,
            "queue_age_source": "job.created_at",
            "required_labels": ["self-hosted", "Linux", "X64", "runnerops"],
            "capacity_status": status,
            "matching_capacity": counts,
            "matching_runner_ids": [],
            "matching_local_runner_names": matching_names,
        }

    def snapshot(self, *, observed_at=None, jobs=1, provisioned=False, matching=False):
        observed_at = observed_at or self.now
        rows = [self.runner("existing", "busy_capacity", 1001)]
        if provisioned:
            rows.append(self.runner("runnerops-auto-01", "provisioned_idle", 2001))
        queue_jobs = []
        for index in range(jobs):
            if matching and provisioned:
                queue_jobs.append(
                    self.job(
                        index,
                        matching_names=["runnerops-auto-01"],
                        status="provisioned_idle",
                    )
                )
            else:
                queue_jobs.append(self.job(index))
        counts = {
            key: sum(row["category"] == key for row in rows)
            for key in ("available_now", "busy_capacity", "provisioned_idle", "inconclusive")
        }
        return {
            "schema_version": 1,
            "kind": "CapacitySnapshot",
            "observed_at": observed_at.isoformat(),
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
                "observed_queued_job_count": len(queue_jobs),
                "queued_job_count": len(queue_jobs),
                "oldest_queued_job_id": queue_jobs[0]["job_id"],
                "oldest_matching_queued_job_id": queue_jobs[0]["job_id"],
                "jobs": queue_jobs,
            },
            "capacity": {
                "counts": counts,
                "runners": rows,
                "matching_basis": "required_labels",
                "remote_scope": "repository_runners_endpoint",
            },
            "host": {
                "active_local_runner_count": 1,
                "observed_active_local_runner_count": 1,
                "status": "complete",
            },
        }

    def seed_pressure(self, *, jobs=1):
        with self.store_factory() as store:
            store.observe(
                self.snapshot(observed_at=self.now - timedelta(seconds=300), jobs=jobs)
            )

    def sequence(self, snapshots):
        values = list(snapshots)
        last = values[-1]

        def collect(_repo):
            nonlocal last
            if values:
                last = values.pop(0)
            return last

        return collect

    def provision(self, repository, target, policy):
        self.provision_calls.append((repository, target, policy["max_local_runners"]))
        return {"status": "ok", "code": "PROVISION_ADD_COMPLETED", "exit_code": 0}

    def run_controller(self, snapshots, **kwargs):
        return run_once(
            ".",
            enabled=True,
            snapshot_fn=self.sequence(snapshots),
            policy_loader=kwargs.pop("policy_loader", lambda: self.policy()),
            host_collector=lambda _policy: {
                "status": "complete",
                "memory_available_mib": 8192,
                "cpu_percent": None,
            },
            store_factory=self.store_factory,
            lock_factory=no_lock,
            start_fn=kwargs.pop(
                "start_fn", lambda target: self.start_calls.append(target) or 0
            ),
            verify_fn=kwargs.pop(
                "verify_fn", lambda _repo, _target, _registration: (True, "VERIFIED_ONLINE")
            ),
            provision_fn=kwargs.pop("provision_fn", self.provision),
            clock=lambda: self.now,
            **kwargs,
        )

    def test_successful_provision_is_audited_end_to_end(self):
        self.seed_pressure()
        absent = self.snapshot()
        ready = self.snapshot(provisioned=True)
        result, code = self.run_controller([absent, absent, ready])

        self.assertEqual(code, 0)
        self.assertEqual(result["decision"], "PROVISION_LOCAL")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["target"], "runnerops-auto-01")
        self.assertEqual(result["action_state"], "succeeded")
        self.assertEqual(len(self.provision_calls), 1)
        self.assertEqual(self.start_calls, [])

        with self.store_factory() as store:
            explanation = store.explain(result["decision_id"])
        action = explanation["actions"][0]
        self.assertEqual(action["kind"], "PROVISION_LOCAL")
        self.assertEqual(action["external_id"], "2001")
        self.assertEqual(
            [event["state"] for event in action["events"]],
            ["planned", "started", "succeeded"],
        )

    def test_requested_delta_greater_than_one_still_provisions_once(self):
        self.seed_pressure(jobs=3)
        absent = self.snapshot(jobs=3)
        ready = self.snapshot(jobs=3, provisioned=True)
        result, code = self.run_controller([absent, absent, ready])

        self.assertEqual(code, 0)
        self.assertEqual(len(self.provision_calls), 1)
        with self.store_factory() as store:
            decision = store.explain(result["decision_id"])["decision"]
        self.assertEqual(decision["requested_capacity_delta"], 3)

    def test_inconclusive_started_action_is_never_blindly_retried(self):
        self.seed_pressure()
        absent = self.snapshot()

        def uncertain(repository, target, policy):
            self.provision_calls.append((repository, target, policy["max_local_runners"]))
            return {"status": "inconclusive", "code": "PROVISION_PARTIAL", "exit_code": 1}

        first, first_code = self.run_controller(
            [absent, absent, absent], provision_fn=uncertain
        )
        self.assertEqual(first_code, 3)
        self.assertEqual(first["action_state"], "started")
        self.assertEqual(len(self.provision_calls), 1)

        self.now += timedelta(seconds=1)
        second_absent = self.snapshot(observed_at=self.now)

        def forbidden(*_args):
            self.fail("started PROVISION_LOCAL must not call add again")

        second, second_code = self.run_controller(
            [second_absent, second_absent], provision_fn=forbidden
        )
        self.assertEqual(second_code, 3)
        self.assertEqual(second["action_state"], "started")
        self.assertEqual(second["target"], "runnerops-auto-01")
        self.assertEqual(len(self.provision_calls), 1)

    def test_successful_provision_hands_next_activation_to_start_local(self):
        self.seed_pressure()
        absent = self.snapshot()
        ready = self.snapshot(provisioned=True)
        first, first_code = self.run_controller([absent, absent, ready])
        self.assertEqual(first_code, 0)
        self.assertEqual(first["decision"], "PROVISION_LOCAL")

        self.now += timedelta(seconds=31)
        idle = self.snapshot(observed_at=self.now, provisioned=True, matching=True)
        second, second_code = self.run_controller([idle, idle])
        self.assertEqual(second_code, 0)
        self.assertEqual(second["decision"], "START_LOCAL")
        self.assertEqual(second["target"], "runnerops-auto-01")
        self.assertEqual(self.start_calls, ["runnerops-auto-01"])
        self.assertEqual(len(self.provision_calls), 1)


if __name__ == "__main__":
    unittest.main()
