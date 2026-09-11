#!/usr/bin/env python3
"""Focused contracts for governed START_LOCAL autoscale mutation."""

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


class ControllerContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / "state" / "autoscale.db"
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.job_created_at = self.now - timedelta(hours=1)
        self.start_calls = []

    def policy(self, **overrides):
        value = {
            "queue_threshold_seconds": 300,
            "max_active_local_runners": 2,
            "min_memory_available_mib": 1024,
            "max_cpu_percent": None,
            "max_burst_runners": 0,
            "cooldown_seconds": 300,
            "burst_enabled": False,
            "label_scope": [],
        }
        value.update(overrides)
        return value

    def store_factory(self):
        return AuditStore(
            self.db,
            writable=True,
            clock=lambda: self.now,
            settings=Settings(queue_gap_seconds=300),
        )

    def runner(self, name, category):
        online = category in ("available_now", "busy_capacity")
        return {
            "name": name,
            "registration_id": abs(hash(name)) % 100000 + 1,
            "scope": "local",
            "enabled": True,
            "local": {
                "unit": f"actions.runner.example.{name}.service",
                "state": "active" if online else "healthy_idle",
                "boot": "disabled",
                "reason": "systemd_active" if online else "on_demand_inactive",
            },
            "github": {
                "id": abs(hash(name)) % 100000 + 1,
                "name": "host-" + name,
                "status": "online" if online else "offline",
                "busy": category == "busy_capacity",
                "labels": ["self-hosted", "Linux", "X64", "runnerops"],
            },
            "category": category,
            "reason": "fixture",
        }

    def snapshot(
        self,
        *,
        category="provisioned_idle",
        names=("runner-a",),
        active_local=0,
        observed_at=None,
        queued=True,
    ):
        observed_at = observed_at or self.now
        runners = [self.runner(name, category) for name in names]
        counts = {
            key: sum(runner["category"] == key for runner in runners)
            for key in ("available_now", "busy_capacity", "provisioned_idle", "inconclusive")
        }
        jobs = []
        if queued:
            jobs.append(
                {
                    "job_id": 101,
                    "run_id": 201,
                    "run_attempt": 1,
                    "status": "queued",
                    "created_at": self.job_created_at.isoformat(),
                    "queue_age_seconds": int((observed_at - self.job_created_at).total_seconds()),
                    "queue_age_source": "job.created_at",
                    "required_labels": ["self-hosted", "Linux", "X64", "runnerops"],
                    "capacity_status": category,
                    "matching_capacity": counts.copy(),
                    "matching_runner_ids": [runner["registration_id"] for runner in runners],
                    "matching_local_runner_names": list(names),
                }
            )
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
                "observed_queued_job_count": len(jobs),
                "queued_job_count": len(jobs),
                "oldest_queued_job_id": 101 if jobs else None,
                "oldest_matching_queued_job_id": 101 if jobs else None,
                "jobs": jobs,
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

    def seed_queue(self, **snapshot_overrides):
        first = self.snapshot(
            observed_at=self.now - timedelta(seconds=300), **snapshot_overrides
        )
        with self.store_factory() as store:
            store.observe(first)

    def start(self, target):
        self.start_calls.append(target)
        return 0

    def run_controller(self, snapshot=None, **kwargs):
        snapshot = snapshot or self.snapshot()
        return run_once(
            ".",
            enabled=True,
            snapshot_fn=lambda _repo: snapshot,
            policy_loader=kwargs.pop("policy_loader", lambda: self.policy()),
            host_collector=lambda _policy: {
                "status": "complete",
                "memory_available_mib": 8192,
                "cpu_percent": None,
            },
            store_factory=self.store_factory,
            lock_factory=no_lock,
            start_fn=kwargs.pop("start_fn", self.start),
            verify_fn=kwargs.pop(
                "verify_fn", lambda _repo, _target: (True, "VERIFIED_ONLINE")
            ),
            clock=lambda: self.now,
            **kwargs,
        )

    def test_exact_idle_runner_is_started_once_and_audited_end_to_end(self):
        self.seed_queue()
        result, code = self.run_controller()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["decision"], "START_LOCAL")
        self.assertEqual(result["action_state"], "succeeded")
        self.assertEqual(self.start_calls, ["runner-a"])

        with self.store_factory() as store:
            explanation = store.explain(result["decision_id"])
        self.assertEqual(len(explanation["actions"]), 1)
        self.assertEqual(
            [event["state"] for event in explanation["actions"][0]["events"]],
            ["planned", "started", "succeeded"],
        )
        self.assertEqual(explanation["actions"][0]["target"], "runner-a")

    def test_shared_group_capacity_starts_only_one_exact_deterministic_runner(self):
        self.seed_queue(names=("runner-z", "runner-a"))
        snapshot = self.snapshot(names=("runner-z", "runner-a"))
        result, code = self.run_controller(snapshot)
        self.assertEqual(code, 0)
        self.assertEqual(result["target"], "runner-a")
        self.assertEqual(self.start_calls, ["runner-a"])
        self.assertNotIn("all", self.start_calls)
        self.assertTrue(all(not target.startswith("group:") for target in self.start_calls))

    def test_start_command_success_but_github_offline_is_failed_not_success(self):
        self.seed_queue()
        result, code = self.run_controller(
            verify_fn=lambda _repo, _target: (False, "GITHUB_VERIFICATION_FAILED")
        )
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["action_state"], "failed")
        self.assertEqual(result["diagnostic"], "GITHUB_VERIFICATION_FAILED")
        with self.store_factory() as store:
            action = store.explain(result["decision_id"])["actions"][0]
        self.assertEqual(action["state"], "failed")
        self.assertEqual(action["diagnostic"]["code"], "GITHUB_VERIFICATION_FAILED")

    def test_policy_change_between_plan_and_apply_rejects_without_start(self):
        self.seed_queue()
        policies = [self.policy(), self.policy(queue_threshold_seconds=301)]

        def load():
            return policies.pop(0) if policies else self.policy(queue_threshold_seconds=301)

        result, code = self.run_controller(policy_loader=load)
        self.assertEqual(code, 3)
        self.assertEqual(result["diagnostic"], "POLICY_CHANGED")
        self.assertEqual(self.start_calls, [])
        with self.store_factory() as store:
            self.assertEqual(store.history()["decisions"], [])

    def test_disabled_controller_does_not_read_or_write_or_lock(self):
        def forbidden(*_args, **_kwargs):
            self.fail("disabled autoscale must not enter runtime dependencies")

        result, code = run_once(
            ".",
            enabled=False,
            snapshot_fn=forbidden,
            policy_loader=forbidden,
            store_factory=forbidden,
            lock_factory=forbidden,
            start_fn=forbidden,
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["diagnostic"], "AUTOSCALE_DISABLED")
        self.assertFalse(self.db.exists())

    def test_other_planner_decisions_remain_read_only_for_lifecycle(self):
        self.seed_queue(category="busy_capacity", active_local=1)
        snapshot = self.snapshot(category="busy_capacity", active_local=1)
        result, code = self.run_controller(snapshot)
        self.assertEqual(code, 0)
        self.assertEqual(result["decision"], "PROVISION_LOCAL")
        self.assertEqual(result["status"], "noop")
        self.assertEqual(self.start_calls, [])
        with self.store_factory() as store:
            self.assertEqual(store.history()["decisions"], [])

    def test_restart_reconciles_started_action_without_duplicate_start_when_online(self):
        self.seed_queue()
        idle = self.snapshot()

        def crash_after_started(target):
            self.start_calls.append(target)
            raise RuntimeError("simulated process death")

        with self.assertRaises(RuntimeError):
            self.run_controller(idle, start_fn=crash_after_started)

        self.now += timedelta(seconds=1)
        online = self.snapshot(
            category="available_now", active_local=1, observed_at=self.now
        )
        result, code = self.run_controller(online)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostic"], "RECOVERED_VERIFIED_ACTION")
        self.assertEqual(self.start_calls, ["runner-a"])
        with self.store_factory() as store:
            rows = store.history()["decisions"]
            self.assertEqual(len(rows), 1)
            action = store.explain(rows[0]["decision_id"])["actions"][0]
        self.assertEqual(action["state"], "succeeded")
        self.assertEqual(
            [event["state"] for event in action["events"]],
            ["planned", "started", "succeeded"],
        )

    def test_restart_with_started_action_still_offline_never_reissues_start(self):
        self.seed_queue()
        idle = self.snapshot()

        def crash_after_started(target):
            self.start_calls.append(target)
            raise RuntimeError("simulated process death")

        with self.assertRaises(RuntimeError):
            self.run_controller(idle, start_fn=crash_after_started)

        self.now += timedelta(seconds=1)
        result, code = self.run_controller(
            self.snapshot(observed_at=self.now),
            verify_fn=lambda _repo, _target: (False, "GITHUB_VERIFICATION_FAILED"),
        )
        self.assertEqual(code, 1)
        self.assertEqual(result["action_state"], "failed")
        self.assertEqual(result["diagnostic"], "GITHUB_VERIFICATION_FAILED")
        self.assertEqual(self.start_calls, ["runner-a"])

    def test_duplicate_iteration_over_same_queue_does_not_start_second_runner(self):
        self.seed_queue()
        snapshot = self.snapshot()
        first, first_code = self.run_controller(snapshot)
        second, second_code = self.run_controller(snapshot)
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(first["decision"], "START_LOCAL")
        self.assertEqual(second["decision"], "HOLD")
        self.assertIn("COOLDOWN_ACTIVE", second["reason_codes"])
        self.assertEqual(self.start_calls, ["runner-a"])


if __name__ == "__main__":
    unittest.main()
