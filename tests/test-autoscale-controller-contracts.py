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

from autoscale_controller import (  # noqa: E402
    ControllerError,
    controller_lock,
    run_once,
    start_exact_runner,
    verify_exact_runner,
)
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

    def registration_id(self, name):
        return sum(ord(char) for char in name) + 1000

    def runner(self, name, category, registration_id=None):
        online = category in ("available_now", "busy_capacity")
        registration_id = registration_id or self.registration_id(name)
        return {
            "name": name,
            "registration_id": registration_id,
            "scope": "local",
            "enabled": True,
            "local": {
                "unit": f"actions.runner.example.{name}.service",
                "state": "active" if online else "healthy_idle",
                "boot": "disabled",
                "reason": "systemd_active" if online else "on_demand_inactive",
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

    def snapshot(
        self,
        *,
        category="provisioned_idle",
        names=("runner-a",),
        active_local=0,
        observed_at=None,
        queued=True,
        registration_ids=None,
    ):
        observed_at = observed_at or self.now
        registration_ids = registration_ids or {}
        runners = [
            self.runner(name, category, registration_ids.get(name))
            for name in names
        ]
        counts = {
            key: sum(runner["category"] == key for runner in runners)
            for key in ("available_now", "busy_capacity", "provisioned_idle", "inconclusive")
        }
        jobs = []
        if queued:
            jobs.append({
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
            })
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

    def snapshot_sequence(self, snapshots):
        snapshots = list(snapshots)
        last = snapshots[-1]

        def collect(_repository):
            nonlocal snapshots, last
            if snapshots:
                last = snapshots.pop(0)
            return last

        return collect

    def run_controller(self, snapshots=None, **kwargs):
        snapshots = list(snapshots or [self.snapshot()])
        return run_once(
            ".",
            enabled=True,
            snapshot_fn=self.snapshot_sequence(snapshots),
            policy_loader=kwargs.pop("policy_loader", lambda: self.policy()),
            host_collector=lambda _policy: {
                "status": "complete",
                "memory_available_mib": 8192,
                "cpu_percent": None,
            },
            store_factory=self.store_factory,
            lock_factory=kwargs.pop("lock_factory", no_lock),
            start_fn=kwargs.pop("start_fn", self.start),
            verify_fn=kwargs.pop(
                "verify_fn",
                lambda _repo, _target, _registration: (True, "VERIFIED_ONLINE"),
            ),
            clock=lambda: self.now,
            **kwargs,
        )

    def test_first_observation_builds_continuity_without_inventing_wait(self):
        result, code = self.run_controller()
        self.assertEqual(code, 0)
        self.assertEqual(result["decision"], "WAIT")
        self.assertIn("QUEUE_BELOW_THRESHOLD", result["reason_codes"])
        self.assertEqual(self.start_calls, [])
        with self.store_factory() as store:
            history = store.history()
        self.assertEqual(history["decisions"], [])
        self.assertEqual(len(history["queue_observations"]), 1)
        row = history["queue_observations"][0]
        self.assertEqual(row["first_seen_queued_at"], row["last_seen_queued_at"])
        self.assertEqual(row["observed_queued_seconds"], 0)

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
        action = explanation["actions"][0]
        self.assertEqual(
            [event["state"] for event in action["events"]],
            ["planned", "started", "succeeded"],
        )
        self.assertEqual(action["target"], "runner-a")
        self.assertEqual(action["external_id"], str(self.registration_id("runner-a")))

    def test_decision_and_started_action_exist_before_lifecycle_call(self):
        self.seed_queue()
        observed = {}

        def inspect_then_start(target):
            with self.store_factory() as store:
                history = store.history()
                self.assertEqual(len(history["decisions"]), 1)
                explanation = store.explain(history["decisions"][0]["decision_id"])
            action = explanation["actions"][0]
            observed["states"] = [event["state"] for event in action["events"]]
            observed["target"] = target
            return 0

        result, code = self.run_controller(start_fn=inspect_then_start)
        self.assertEqual(code, 0)
        self.assertEqual(result["action_state"], "succeeded")
        self.assertEqual(observed["states"], ["planned", "started"])
        self.assertEqual(observed["target"], "runner-a")

    def test_shared_capacity_starts_only_one_exact_deterministic_runner(self):
        self.seed_queue(names=("runner-z", "runner-a"))
        snapshot = self.snapshot(names=("runner-z", "runner-a"))
        result, code = self.run_controller([snapshot])
        self.assertEqual(code, 0)
        self.assertEqual(result["target"], "runner-a")
        self.assertEqual(self.start_calls, ["runner-a"])
        self.assertNotIn("all", self.start_calls)
        self.assertTrue(all(not target.startswith("group:") for target in self.start_calls))

    def test_invalid_broad_targets_are_rejected_by_lifecycle_boundary(self):
        for target in ("all", "group:runnerops"):
            with self.subTest(target=target):
                with self.assertRaises(ControllerError):
                    start_exact_runner(target)

    def test_start_zero_but_github_offline_is_failed(self):
        self.seed_queue()
        result, code = self.run_controller(
            verify_fn=lambda _repo, _target, _registration: (
                False,
                "GITHUB_VERIFICATION_FAILED",
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["action_state"], "failed")
        self.assertEqual(result["diagnostic"], "GITHUB_VERIFICATION_FAILED")

    def test_nonzero_start_can_succeed_only_after_structured_verification(self):
        self.seed_queue()
        result, code = self.run_controller(
            start_fn=lambda target: self.start_calls.append(target) or 7,
            verify_fn=lambda _repo, _target, _registration: (
                True,
                "VERIFIED_ONLINE",
            ),
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostic"], "VERIFIED_ONLINE_AFTER_START_ERROR")
        with self.store_factory() as store:
            action = store.explain(result["decision_id"])["actions"][0]
        self.assertEqual(action["state"], "succeeded")
        self.assertEqual(action["diagnostic"]["exit_code"], 7)

    def test_structured_verification_accepts_online_busy_exact_registration(self):
        busy = self.snapshot(category="busy_capacity", active_local=1, queued=False)
        registration_id = str(self.registration_id("runner-a"))
        verified, code = verify_exact_runner(
            "Example/RunnerOps",
            "runner-a",
            registration_id,
            snapshot_fn=lambda _repo: busy,
        )
        self.assertTrue(verified)
        self.assertEqual(code, "VERIFIED_ONLINE")

    def test_policy_change_after_decision_persists_decision_but_no_action(self):
        self.seed_queue()
        policies = [self.policy(), self.policy(queue_threshold_seconds=301)]

        def load():
            return policies.pop(0) if policies else self.policy(queue_threshold_seconds=301)

        result, code = self.run_controller(policy_loader=load)
        self.assertEqual(code, 0)
        self.assertEqual(result["diagnostic"], "POLICY_CHANGED")
        self.assertEqual(self.start_calls, [])
        with self.store_factory() as store:
            history = store.history()
            self.assertEqual(len(history["decisions"]), 1)
            explanation = store.explain(history["decisions"][0]["decision_id"])
        self.assertEqual(explanation["actions"], [])

    def test_queue_change_during_revalidation_performs_no_mutation(self):
        self.seed_queue()
        initial = self.snapshot()
        self.now += timedelta(seconds=1)
        fresh = self.snapshot(queued=False)
        result, code = self.run_controller([initial, fresh])
        self.assertEqual(code, 0)
        self.assertEqual(result["diagnostic"], "PLAN_CHANGED")
        self.assertEqual(self.start_calls, [])
        with self.store_factory() as store:
            explanation = store.explain(result["decision_id"])
        self.assertEqual(explanation["actions"], [])

    def test_registration_identity_change_during_revalidation_performs_no_mutation(self):
        self.seed_queue()
        initial = self.snapshot(registration_ids={"runner-a": 1111})
        fresh = self.snapshot(registration_ids={"runner-a": 2222})
        result, code = self.run_controller([initial, fresh])
        self.assertEqual(code, 0)
        self.assertEqual(result["diagnostic"], "TARGET_CHANGED")
        self.assertEqual(self.start_calls, [])

    def test_lock_contention_happens_after_decision_and_creates_no_action(self):
        self.seed_queue()

        @contextmanager
        def busy_lock():
            raise ControllerError("CONTROLLER_BUSY")
            yield

        result, code = self.run_controller(lock_factory=busy_lock)
        self.assertEqual(code, 3)
        self.assertEqual(result["diagnostic"], "CONTROLLER_BUSY")
        self.assertEqual(self.start_calls, [])
        with self.store_factory() as store:
            history = store.history()
            self.assertEqual(len(history["decisions"]), 1)
            explanation = store.explain(history["decisions"][0]["decision_id"])
        self.assertEqual(explanation["actions"], [])

    def test_disabled_controller_does_not_read_write_or_lock(self):
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

    def test_other_planner_decisions_remain_non_mutating(self):
        self.seed_queue(category="busy_capacity", active_local=1)
        snapshot = self.snapshot(category="busy_capacity", active_local=1)
        result, code = self.run_controller([snapshot])
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
            self.run_controller([idle], start_fn=crash_after_started)

        self.now += timedelta(seconds=1)
        online = self.snapshot(
            category="available_now", active_local=1, observed_at=self.now
        )
        result, code = self.run_controller([online])
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
            self.run_controller([idle], start_fn=crash_after_started)

        self.now += timedelta(seconds=1)
        result, code = self.run_controller(
            [self.snapshot(observed_at=self.now)],
            verify_fn=lambda _repo, _target, _registration: (
                False,
                "GITHUB_VERIFICATION_FAILED",
            ),
        )
        self.assertEqual(code, 1)
        self.assertEqual(result["action_state"], "failed")
        self.assertEqual(result["diagnostic"], "GITHUB_VERIFICATION_FAILED")
        self.assertEqual(self.start_calls, ["runner-a"])

    def test_controller_lock_rejects_concurrent_mutator(self):
        lock = self.base / "lock-state" / "autoscale-controller.lock"
        with controller_lock(lock):
            with self.assertRaises(ControllerError) as raised:
                with controller_lock(lock):
                    pass
        self.assertEqual(raised.exception.code, "CONTROLLER_BUSY")

    def test_duplicate_iteration_over_same_queue_does_not_start_second_runner(self):
        self.seed_queue()
        snapshot = self.snapshot()
        first, first_code = self.run_controller([snapshot])
        second, second_code = self.run_controller([snapshot])
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(first["decision"], "START_LOCAL")
        self.assertEqual(second["decision"], "HOLD")
        self.assertIn("COOLDOWN_ACTIVE", second["reason_codes"])
        self.assertEqual(self.start_calls, ["runner-a"])


if __name__ == "__main__":
    unittest.main()
