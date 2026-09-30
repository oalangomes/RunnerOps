#!/usr/bin/env python3
"""Failure and reconciliation contracts for one exact ephemeral lifecycle."""

import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from runnerops.ephemeral.contracts import LifecycleState
from runnerops.ephemeral.lifecycle import CleanupRefused, EphemeralLifecycle, ReconcileRequired
from runnerops.ephemeral.runtime import RuntimeFailure
from runnerops.ephemeral.store import ActionStore


ACTION_ID = "11111111111111111111111111111111"


class FakeLocal:
    def __init__(self, root):
        self.ephemeral_root = Path(root)
        self.status = "ALLOCATED"
        self.config_present = False
        self.configure_fails = False
        self.preflight_fails = False
        self.ensure_fails = False
        self.allocate_fails = False
        self.materialize_fails = False
        self.remove_fails = False
        self.configure_calls = 0
        self.removed = 0
        self.stopped = 0

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
        self.status = "CONFIGURED"

    def start(self, action):
        self.status = "RUNNING"
        return {"status": "RUNNING", "systemd_unit": "actions.runner.fixture.service",
                "active_state": "active", "main_pid": 1234}

    def observe(self, action):
        return {"status": self.status, "systemd_unit": "actions.runner.fixture.service",
                "active_state": "active" if self.status == "RUNNING" else "inactive",
                "main_pid": 1234 if self.status == "RUNNING" else None,
                "config_present": self.config_present,
                "config_identity": action.runner_identity if self.config_present else None,
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

    @staticmethod
    def observation(status):
        present = status in ("ONLINE", "OFFLINE", "BUSY")
        return {"status": status, "runner_id": 42 if present else None,
                "remote_status": "online" if status in ("ONLINE", "BUSY") else
                                 ("offline" if status == "OFFLINE" else None),
                "busy": status == "BUSY", "reason": "fake_remote"}

    def queue(self, *statuses):
        self.observations.extend(self.observation(status) for status in statuses)

    def observe_runner(self, repository, identity):
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
        self.lifecycle = EphemeralLifecycle(self.store, self.local, self.github, sleeper=lambda _: None)

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, final_remote="ONLINE", action_id=ACTION_ID):
        self.github.queue("ABSENT", final_remote)
        return self.lifecycle.create(
            "Example/Repo", "generic", ["repo", "local-runner", "ephemeral"],
            action_id=action_id, online_timeout=0,
        )

    def test_registration_is_exact_and_online_is_observed_not_assumed(self):
        action = self.create("ONLINE")
        self.assertEqual(action.action_state, LifecycleState.ONLINE.value)
        self.assertEqual(action.github_observation["status"], "ONLINE")
        self.assertTrue(action.registration["attempted"])
        self.assertEqual(action.registration["attempts"], 1)
        self.assertFalse(action.workload_evidence["observed"])

    def test_requested_crash_before_remote_mutation_resumes_same_action(self):
        initial = self.lifecycle._new_action("Example/Repo", "generic", ["ephemeral"], ACTION_ID)
        self.assertFalse(initial.registration["attempted"])
        self.github.queue("ABSENT", "ONLINE")
        resumed = self.lifecycle.create(
            "Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID, online_timeout=0)
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
        reconciled = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(reconciled.action_state, LifecycleState.REQUESTED.value)
        self.assertTrue(reconciled.registration["safe_retry_authorized"])

        self.github.queue("ABSENT", "ONLINE")
        retried = self.lifecycle.create("Example/Repo", "generic", ["ephemeral"], action_id=ACTION_ID,
                                        online_timeout=0)
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

    def test_local_exit_alone_does_not_prove_terminal(self):
        self.create("ONLINE")
        self.local.status = "EXITED"
        self.github.queue("ONLINE")
        action = self.lifecycle.reconcile(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.INCONCLUSIVE_TERMINAL.value)
        self.assertFalse(action.terminal_evidence["proven"])

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
        self.github.queue("OFFLINE")
        first = self.lifecycle.cleanup(ACTION_ID)
        second = self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(first.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(second.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(second.cleanup["attempts"], 1)
        self.assertEqual(self.github.delete_calls, 1)
        self.assertEqual(self.local.removed, 1)

    def test_interrupted_cleanup_can_continue(self):
        self.create("ONLINE")
        self.local.status = "EXITED"
        self.local.remove_fails = True
        self.github.queue("OFFLINE")
        with self.assertRaises(RuntimeFailure):
            self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(self.store.load(ACTION_ID).action_state, LifecycleState.CLEANUP_PENDING.value)
        self.github.queue("ABSENT")
        action = self.lifecycle.cleanup(ACTION_ID)
        self.assertEqual(action.action_state, LifecycleState.CLEANED.value)
        self.assertEqual(action.cleanup["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
