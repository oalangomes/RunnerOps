#!/usr/bin/env python3
"""Failure and reconciliation contracts for one exact ephemeral lifecycle."""

import tempfile
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from runnerops.ephemeral.contracts import LifecycleState
from runnerops.ephemeral.lifecycle import CleanupRefused, EphemeralLifecycle, ReconcileRequired
from runnerops.ephemeral.runtime import RuntimeFailure
from runnerops.ephemeral.store import ActionStore


ACTION_ID = "11111111111111111111111111111111"


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value.isoformat()

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeLocal:
    def __init__(self, root):
        self.ephemeral_root = Path(root)
        self.status = "ALLOCATED"
        self.config_present = False
        self.config_identity = None
        self.config_runner_id = 42
        self.configure_fails = False
        self.preflight_fails = False
        self.ensure_fails = False
        self.allocate_fails = False
        self.materialize_fails = False
        self.remove_fails = False
        self.start_fails = False
        self.configure_calls = 0
        self.removed = 0
        self.stopped = 0
        self.start_calls = 0
        self.observe_calls = 0

    def preflight(self, action):
        if self.preflight_fails:
            raise RuntimeFailure("simulated systemd preflight failure")

    def ensure_package(self, version, arch):
        if self.ensure_fails:
            raise RuntimeFailure("simulated package failure")
        path = self.ephemeral_root.parent / "runner.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"verified")
        return path

    def allocate_root(self, action):
        if self.allocate_fails:
            raise RuntimeFailure("simulated unsafe root")
        Path(action.disposable_root).mkdir(parents=True, exist_ok=True)

    def materialize(self, action, package):
        if self.materialize_fails:
            raise RuntimeFailure("simulated extraction failure")
        return None

    def configure(self, action, registration_material):
        self.configure_calls += 1
        if self.configure_fails:
            raise RuntimeFailure("simulated uncertain configure")
        self.config_present = True
        self.config_identity = action.runner_identity
        self.status = "CONFIGURED"

    def start(self, action):
        self.start_calls += 1
        if self.start_fails:
            raise RuntimeFailure("simulated local start failure")
        self.status = "RUNNING"
        return {"status": "RUNNING", "systemd_unit": "actions.runner.fixture.service",
                "active_state": "active", "main_pid": 1234}

    def observe(self, action):
        self.observe_calls += 1
        return {"status": self.status, "systemd_unit": "actions.runner.fixture.service",
                "active_state": "active" if self.status == "RUNNING" else "inactive",
                "main_pid": 1234 if self.status == "RUNNING" else None,
                "config_present": self.config_present,
                "config_identity": (self.config_identity or action.runner_identity)
                                   if self.config_present else None,
                "config_runner_id": self.config_runner_id if self.config_present else None,
                "reason": "fake_local"}

    def validate_owned_root(self, action, allow_missing=True):
        return Path(action.disposable_root)

    def stop(self, action):
        self.stopped += 1
        self.status = "EXITED"

    def remove_root(self, action):
        if self.remove_fails:
            self.remove_fails = False
            raise RuntimeFailure("simulated interrupted cleanup")
        self.removed += 1
        self.status = "ABSENT"


class FakeGitHub:
    def __init__(self):
        self.observations = []
        self.current = self.observation("ABSENT")
        self.token_calls = 0
        self.delete_calls = 0
        self.token_fails = False
        self.observed_identities = []
        self.observe_calls = 0

    @staticmethod
    def observation(status):
        present = status in ("ONLINE", "OFFLINE", "BUSY")
        return {"status": status, "runner_id": 42 if present else None,
                "ephemeral": True if present else None,
                "remote_status": "online" if status in ("ONLINE", "BUSY") else
                                 ("offline" if status == "OFFLINE" else None),
                "busy": status == "BUSY", "reason": "fake_remote"}

    def queue(self, *statuses):
        self.observations.extend(self.observation(status) for status in statuses)

    def observe_runner(self, repository, identity):
        self.observe_calls += 1
        self.observed_identities.append(identity)
        if self.observations:
            self.current = self.observations.pop(0)
        return dict(self.current)

    def request_registration_material(self, repository):
        self.token_calls += 1
        if self.token_fails:
            raise RuntimeFailure("simulated registration material failure")
        return "short-lived-material"

    def delete_runner(self, repository, runner_id):
        self.delete_calls += 1
        self.current = self.observation("ABSENT")


class LifecycleContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.store = ActionStore(base / "state")
        self.local = FakeLocal(base / "data" / ".ephemeral")
        self.github = FakeGitHub()
        self.clock = MutableClock()
        self.monotonic_value = 0.0

        def monotonic():
            return self.monotonic_value

        def sleeper(seconds):
            self.monotonic_value += seconds

        self.lifecycle = EphemeralLifecycle(
            self.store, self.local, self.github, clock=self.clock,
            monotonic=monotonic, sleeper=sleeper,
            registration_absence_confirm_seconds=3)

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, final_remote="ONLINE", action_id=ACTION_ID):
        self.github.queue("ABSENT", final_remote)
        return self.lifecycle.create(
            "Example/Repo", "generic", ["repo", "local-runner", "ephemeral"],
            action_id=action_id, online_timeout=0.1,
        )

    def test_registration_is_exact_and_online_is_observed_not_assumed(self):
        action = self.create("ONLINE")
        self.assertEqual(action.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(action.github_observation["status"], "ONLINE")
        self.assertTrue(action.registration["attempted"])
        self.assertEqual(action.registration["attempts"], 1)
        self.assertFalse(action.workload_evidence["observed"])

    def test_direct_lifecycle_durations_fail_before_runtime_mutation(self):
        invalid_values = (float("nan"), float("inf"), float("-inf"), 0, -1)
        for parameter in ("online_timeout", "observation_interval"):
            for value in invalid_values:
                with self.subTest(parameter=parameter, value=value):
                    arguments = {"online_timeout": 1.0, "observation_interval": 1.0}
                    arguments[parameter] = value
                    with self.assertRaisesRegex(ValueError, "finite positive duration"):
                        self.lifecycle.create(
                            "Example/Repo", "generic", ["ephemeral"],
                            action_id=ACTION_ID, **arguments)
                    self.assertFalse(self.store.exists(ACTION_ID))
                    self.assertEqual(self.github.observe_calls, 0)

    def test_registration_absence_window_requires_finite_positive_duration(self):
        for value in (float("nan"), float("inf"), float("-inf"), 0, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite positive duration"):
                    EphemeralLifecycle(
                        self.store, self.local, self.github,
                        registration_absence_confirm_seconds=value)

    def test_requested_crash_before_remote_mutation_resumes_same_action(self):
        initial = self.lifecycle._new_action("Example/Repo", "generic", ["ephemeral"], ACTION_ID)
        self.assertFalse(initial.registration["attempted"])
        self.github.queue("ABSENT", "ONLINE")
        resumed = self.lifecycle.create(
            "Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID, online_timeout=0.1)
        self.assertEqual(resumed.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(resumed.action_id, ACTION_ID)
        self.assertEqual(resumed.registration["attempts"], 1)

    def test_registered_but_not_online_is_explicitly_inconclusive(self):
        action = self.create("OFFLINE")
        self.assertEqual(action.action_state, LifecycleState.INCONCLUSIVE_ONLINE.value)
        self.assertEqual(action.final_reason, "exact_runner_not_online_within_bound")
        self.assertEqual(self.github.token_calls, 1)

    def test_package_setup_and_unsafe_root_fail_before_remote_mutation(self):
        for attribute in ("preflight_fails", "ensure_fails", "allocate_fails", "materialize_fails"):
            with self.subTest(attribute=attribute):
                self.tearDown()
                self.setUp()
                setattr(self.local, attribute, True)
                with self.assertRaises(RuntimeFailure):
                    self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID)
                action = self.store.load(ACTION_ID)
                self.assertEqual(action.action_state, LifecycleState.TERMINAL.value)
                self.assertFalse(action.registration["attempted"])
                self.assertEqual(self.github.token_calls, 0)
                self.assertEqual(self.local.configure_calls, 0)

    def test_registration_material_failure_is_pre_mutation_terminal(self):
        self.github.queue("ABSENT")
        self.github.token_fails = True
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID)
        action = self.store.load(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.TERMINAL.value)
        self.assertFalse(action.registration["attempted"])
        self.assertEqual(self.local.configure_calls, 0)

    def test_exact_remote_collision_never_auto_increments_or_requests_material(self):
        self.github.queue("ONLINE")
        with self.assertRaises(ReconcileRequired):
            self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID)
        action = self.store.load(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.INCONCLUSIVE_REGISTRATION.value)
        self.assertEqual(self.github.token_calls, 0)
        self.assertTrue(all(identity == action.runner_identity for identity in self.github.observed_identities))
        self.assertNotRegex(action.runner_identity, r"-2$")

    def test_registration_uncertainty_requires_reconcile_before_retry(self):
        self.github.queue("ABSENT")
        self.local.configure_fails = True
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID)
        action = self.store.load(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.INCONCLUSIVE_REGISTRATION.value)
        self.assertTrue(action.registration["reconcile_required"])
        self.assertEqual(self.github.token_calls, 1)

        with self.assertRaises(ReconcileRequired):
            self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID)
        self.assertEqual(self.github.token_calls, 1, "blind retry must not request new material")

        self.local.configure_fails = False
        self.local.status = "ALLOCATED"
        self.local.config_present = False
        self.github.queue("ABSENT")
        first_reconcile = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(first_reconcile.action_state, LifecycleState.INCONCLUSIVE_REGISTRATION.value)
        self.assertFalse(first_reconcile.registration["safe_retry_authorized"])
        self.github.queue("ABSENT")
        immediate = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(immediate.action_state, LifecycleState.INCONCLUSIVE_REGISTRATION.value)
        self.assertFalse(immediate.registration["safe_retry_authorized"])
        self.clock.advance(3)
        self.github.queue("ABSENT")
        reconciled = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(reconciled.action_state, LifecycleState.REQUESTED.value)
        self.assertTrue(reconciled.registration["safe_retry_authorized"])

        self.github.queue("ABSENT", "ONLINE")
        retried = self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID,
                                        online_timeout=0.1)
        self.assertEqual(retried.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(self.github.token_calls, 2)
        self.assertEqual(retried.registration["attempts"], 2)

    def test_online_without_job_does_not_fabricate_busy(self):
        self.create("ONLINE")
        self.github.queue("ONLINE")
        action = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.ONLINE.value)
        self.assertFalse(action.workload_evidence["observed"])

    def test_registration_uncertainty_recovers_exact_remote_without_new_material(self):
        self.github.queue("ABSENT")
        self.local.configure_fails = True
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID)
        self.local.configure_fails = False
        self.local.config_present = True
        self.local.config_identity = self.store.load(ACTION_ID).runner_identity
        self.local.status = "CONFIGURED"
        self.github.queue("ONLINE")
        action = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.ONLINE.value)
        self.assertFalse(action.registration["reconcile_required"])
        self.assertEqual(self.github.token_calls, 1)
        self.assertEqual(self.local.status, "RUNNING")

    def test_crash_persisted_registering_is_reconciled_as_uncertain(self):
        action = self.lifecycle._new_action("Example/Repo", "generic", ["ephemeral"], ACTION_ID)
        action.registration.update({"attempted": True, "attempts": 1,
                                    "last_attempt_at": action.updated_at})
        action.transition(LifecycleState.REGISTERING, action.updated_at, "registration_initiated")
        self.store.save(action)
        self.local.status = "ALLOCATED"
        self.local.config_present = False
        self.github.queue("ABSENT")

        reconciled = self.lifecycle.reconcile(ACTION_ID)

        self.assertEqual(reconciled.action_state, LifecycleState.INCONCLUSIVE_REGISTRATION.value)
        self.assertTrue(reconciled.registration["reconcile_required"])
        self.assertEqual(reconciled.registration["consecutive_absence_observations"], 1)
        self.assertEqual(self.github.token_calls, 0)

    def test_crash_after_registered_before_start_is_recovered_without_registration(self):
        action = self.lifecycle._new_action("Example/Repo", "generic", ["ephemeral"], ACTION_ID)
        action.registration.update({"attempted": True, "attempts": 1, "configured": True})
        action.transition(LifecycleState.REGISTERED, action.updated_at, "local_configuration_succeeded")
        self.store.save(action)
        self.local.status = "CONFIGURED"
        self.local.config_present = True
        self.local.config_identity = action.runner_identity
        self.github.queue("ONLINE")

        reconciled = self.lifecycle.reconcile(ACTION_ID)

        self.assertEqual(reconciled.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(self.local.status, "RUNNING")
        self.assertEqual(self.github.token_calls, 0)

    def test_busy_evidence_blocks_destructive_cleanup(self):
        action = self.create("BUSY")
        self.assertEqual(action.action_state, LifecycleState.BUSY.value)
        self.github.queue("BUSY")
        with self.assertRaises(CleanupRefused):
            self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(self.local.removed, 0)
        self.assertEqual(self.github.delete_calls, 0)

    def test_local_exit_before_workload_recovers_configured_start(self):
        self.create("ONLINE")
        self.local.status = "EXITED"
        self.github.queue("ONLINE")
        action = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(self.local.status, "RUNNING")
        self.assertFalse(action.terminal_evidence["proven"])

    def test_online_idle_and_offline_cleanup_refuse_without_terminal_proof(self):
        for remote in ("ONLINE", "OFFLINE"):
            with self.subTest(remote=remote):
                self.tearDown()
                self.setUp()
                self.create("ONLINE")
                self.local.status = "EXITED"
                self.github.queue(remote)
                with self.assertRaises(CleanupRefused):
                    self.lifecycle.cleanup(ACTION_ID)
                self.assertEqual(self.github.delete_calls, 0)
                self.assertEqual(self.local.stopped, 0)
                self.assertEqual(self.local.removed, 0)

    def test_proven_terminal_cleanup_refuses_fresh_remote_presence(self):
        for remote in ("ONLINE", "OFFLINE"):
            with self.subTest(remote=remote):
                self.tearDown()
                self.setUp()
                self.create("ONLINE")
                self.local.status = "EXITED"
                terminal = self.store.load(ACTION_ID)
                terminal.terminal_evidence.update({"proven": True, "reason": "test_proof"})
                terminal.transition(LifecycleState.TERMINAL, self.clock(), "test_terminal")
                self.store.save(terminal)
                self.github.queue(remote)

                with self.assertRaises(CleanupRefused):
                    self.lifecycle.cleanup(ACTION_ID)

                refused = self.store.load(ACTION_ID)
                self.assertEqual(
                    refused.cleanup["reason"],
                    "terminal_evidence_contradicted_by_remote_state",
                )
                self.assertEqual(refused.action_state, LifecycleState.INCONCLUSIVE_TERMINAL.value)
                self.assertEqual(self.github.delete_calls, 0)
                self.assertEqual(self.local.stopped, 0)
                self.assertEqual(self.local.removed, 0)

    def test_proven_terminal_cleanup_refuses_fresh_active_local_state(self):
        for local_status in ("RUNNING", "STARTING"):
            with self.subTest(local_status=local_status):
                self.tearDown()
                self.setUp()
                self.create("ONLINE")
                terminal = self.store.load(ACTION_ID)
                terminal.terminal_evidence.update({"proven": True, "reason": "test_proof"})
                terminal.transition(LifecycleState.TERMINAL, self.clock(), "test_terminal")
                self.store.save(terminal)
                self.local.status = local_status
                self.github.queue("ABSENT")

                with self.assertRaises(CleanupRefused):
                    self.lifecycle.cleanup(ACTION_ID)

                refused = self.store.load(ACTION_ID)
                self.assertEqual(
                    refused.cleanup["reason"],
                    "terminal_evidence_contradicted_by_local_state",
                )
                self.assertEqual(refused.action_state, LifecycleState.INCONCLUSIVE_TERMINAL.value)
                self.assertEqual(self.github.delete_calls, 0)
                self.assertEqual(self.local.stopped, 0)
                self.assertEqual(self.local.removed, 0)

    def test_identity_mismatch_is_inconclusive_and_cleanup_never_deletes(self):
        self.create("ONLINE")
        self.local.status = "EXITED"
        self.local.config_runner_id = 99
        action = self.store.load(ACTION_ID)
        action.terminal_evidence["proven"] = True
        self.store.save(action)
        self.github.queue("OFFLINE")
        with self.assertRaises(CleanupRefused):
            self.lifecycle.cleanup(ACTION_ID)
        persisted = self.store.load(ACTION_ID)
        self.assertEqual(persisted.github_observation["status"], "UNKNOWN")
        self.assertEqual(self.github.delete_calls, 0)

    def test_non_ephemeral_remote_is_inconclusive_and_cleanup_never_deletes(self):
        self.create("ONLINE")
        action = self.store.load(ACTION_ID)
        action.terminal_evidence["proven"] = True
        self.store.save(action)
        remote = self.github.observation("OFFLINE")
        remote["ephemeral"] = False
        self.github.observations.append(remote)
        with self.assertRaises(CleanupRefused):
            self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(self.github.delete_calls, 0)

    def test_remote_disappearance_alone_does_not_prove_local_terminal(self):
        self.create("ONLINE")
        self.github.queue("ABSENT")
        action = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.INCONCLUSIVE_TERMINAL.value)
        self.assertEqual(action.local_observation["status"], "RUNNING")

    def test_terminal_requires_workload_local_exit_and_remote_absence(self):
        self.create("BUSY")
        self.local.status = "EXITED"
        self.config_present = False
        self.github.queue("ABSENT")
        action = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.TERMINAL.value)
        self.assertTrue(action.terminal_evidence["proven"])
        self.assertEqual(action.terminal_evidence["job_conclusion"], "unknown")

    def test_cleanup_is_idempotent_and_converges(self):
        self.create("ONLINE")
        self.local.status = "EXITED"
        terminal = self.store.load(ACTION_ID)
        terminal.terminal_evidence.update({"proven": True, "reason": "test_proof"})
        terminal.transition(LifecycleState.TERMINAL, self.clock(), "test_terminal")
        self.store.save(terminal)
        self.github.queue("ABSENT")
        first = self.lifecycle.cleanup(ACTION_ID)
        second = self.lifecycle.cleanup(ACTION_ID)
        local_observations = self.local.observe_calls
        remote_observations = self.github.observe_calls
        status = self.lifecycle.status(ACTION_ID)
        reconciled = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(first.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(second.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(status.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(reconciled.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(second.cleanup["attempts"], 1)
        self.assertEqual(self.github.delete_calls, 0)
        self.assertEqual(self.local.removed, 1)
        self.assertEqual(self.local.observe_calls, local_observations)
        self.assertEqual(self.github.observe_calls, remote_observations)

    def test_interrupted_cleanup_can_continue(self):
        self.create("ONLINE")
        self.local.status = "EXITED"
        self.local.remove_fails = True
        terminal = self.store.load(ACTION_ID)
        terminal.terminal_evidence.update({"proven": True, "reason": "test_proof"})
        terminal.transition(LifecycleState.TERMINAL, self.clock(), "test_terminal")
        self.store.save(terminal)
        self.github.queue("ABSENT")
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(self.store.load(ACTION_ID).action_state, LifecycleState.CLEANUP_PENDING.value)
        self.github.queue("ABSENT")
        action = self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(action.cleanup["attempts"], 2)

    def test_configured_start_failure_recovers_without_registration_retry(self):
        self.github.queue("ABSENT")
        self.local.start_fails = True
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.create(
                "Example/Repo", "generic", ["ephemeral"],
                action_id=ACTION_ID, online_timeout=0.1)
        failed = self.store.load(ACTION_ID)
        self.assertEqual(failed.action_state, LifecycleState.INCONCLUSIVE_TERMINAL.value)
        self.assertTrue(failed.registration["configured"])
        self.local.start_fails = False
        self.github.queue("ONLINE")
        recovered = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(recovered.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(self.github.token_calls, 1)
        self.assertEqual(recovered.registration["attempts"], 1)

    def test_repeated_configured_start_failure_remains_inconclusive(self):
        self.github.queue("ABSENT")
        self.local.start_fails = True
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.create(
                "Example/Repo", "generic", ["ephemeral"],
                action_id=ACTION_ID, online_timeout=0.1)
        self.github.queue("ONLINE")
        failed_again = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(failed_again.action_state, LifecycleState.INCONCLUSIVE_TERMINAL.value)
        self.assertEqual(self.github.token_calls, 1)
        self.assertEqual(failed_again.registration["attempts"], 1)

    def test_registration_absence_confirmation_resets_on_interruption(self):
        action = self.lifecycle._new_action("Example/Repo", "generic", ["ephemeral"], ACTION_ID)
        action.registration.update({"attempted": True, "attempts": 1,
                                    "uncertainty": True, "reconcile_required": True})
        action.transition(LifecycleState.INCONCLUSIVE_REGISTRATION, self.clock(), "test_uncertain")
        self.store.save(action)
        self.github.queue("ABSENT")
        first = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(first.registration["consecutive_absence_observations"], 1)
        self.clock.advance(4)
        self.github.queue("UNKNOWN")
        interrupted = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(interrupted.registration["consecutive_absence_observations"], 0)
        self.assertIsNone(interrupted.registration["first_absence_observed_at"])
        self.github.queue("ABSENT")
        restarted = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(restarted.registration["consecutive_absence_observations"], 1)
        self.assertFalse(restarted.registration["safe_retry_authorized"])


if __name__ == "__main__":
    unittest.main()
