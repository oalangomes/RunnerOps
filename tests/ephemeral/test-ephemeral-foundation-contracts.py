#!/usr/bin/env python3
"""Contracts for exact identity and non-secret durable evidence."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from runnerops.ephemeral.contracts import EphemeralAction, LifecycleState
from runnerops.ephemeral.identity import disposable_root, runner_identity
from runnerops.ephemeral.store import ActionLocked, ActionStore


ACTION_ID = "0123456789abcdef0123456789abcdef"


class FoundationContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def action(self):
        now = datetime.now(timezone.utc).isoformat()
        root = disposable_root(self.base / "data" / ".ephemeral", ACTION_ID)
        action = EphemeralAction(
            action_id=ACTION_ID,
            runner_identity=runner_identity(ACTION_ID),
            disposable_root=str(root),
            repository="Example/Repo",
            profile="generic",
            labels=["self-hosted", "ephemeral"],
            created_at=now,
            updated_at=now,
        )
        action.transitions.append({"from": None, "to": "REQUESTED", "at": now, "reason": "requested"})
        return action

    def test_action_maps_to_one_deterministic_exact_identity(self):
        identity = runner_identity(ACTION_ID)
        self.assertEqual(identity, runner_identity(ACTION_ID))
        self.assertRegex(identity, r"^runnerops-ephemeral-[a-f0-9]{16}$")
        self.assertNotRegex(identity, r"-2$")

    def test_disposable_root_is_exact_action_child(self):
        ephemeral_root = self.base / "runners" / ".ephemeral"
        self.assertEqual(disposable_root(ephemeral_root, ACTION_ID), ephemeral_root / ACTION_ID)
        with self.assertRaises(ValueError):
            disposable_root(ephemeral_root, "../persistent-runner")

    def test_store_round_trips_separate_lifecycle_dimensions(self):
        store = ActionStore(self.base / "state")
        action = self.action()
        action.transition(LifecycleState.REGISTERING, action.updated_at, "registration_started")
        path = store.save(action)
        loaded = store.load(ACTION_ID)
        self.assertEqual(loaded.action_state, "REGISTERING")
        self.assertEqual(loaded.desired_state, "ONE_JOB_TERMINAL_AND_CLEANED")
        self.assertEqual(loaded.local_observation["status"], "UNKNOWN")
        self.assertEqual(loaded.github_observation["status"], "UNKNOWN")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_secret_bearing_evidence_is_rejected(self):
        store = ActionStore(self.base / "state")
        action = self.action()
        action.registration["registration_token"] = "must-never-persist"
        with self.assertRaisesRegex(ValueError, "secret-bearing key"):
            store.save(action)
        self.assertFalse(store.path_for(ACTION_ID).exists())

    def test_serialized_contract_has_no_secret_material(self):
        store = ActionStore(self.base / "state")
        path = store.save(self.action())
        payload = path.read_text(encoding="utf-8")
        self.assertNotIn("registration_token", payload)
        self.assertNotIn("must-never-persist", payload)
        self.assertEqual(json.loads(payload)["kind"], "EphemeralAction")

    def test_same_action_operations_are_fenced_by_bounded_lock(self):
        store = ActionStore(self.base / "state")
        with store.lock(ACTION_ID):
            with self.assertRaises(ActionLocked):
                with store.lock(ACTION_ID, timeout=0):
                    self.fail("same action lock must not be acquired concurrently")


if __name__ == "__main__":
    unittest.main()
