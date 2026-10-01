#!/usr/bin/env python3
"""Contracts for exact identity and non-secret durable evidence."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from runnerops.ephemeral.cli import _runtime, build_parser
from runnerops.ephemeral.contracts import EphemeralAction, LifecycleState
from runnerops.ephemeral.identity import disposable_root, runner_identity
from runnerops.ephemeral.store import ActionLocked, ActionStore


ACTION_ID = "0123456789abcdef0123456789abcdef"
INVALID_DURATIONS = ("nan", "NaN", "inf", "+inf", "-inf", "infinity", "0", "-1")
VALID_DURATIONS = ("0.1", "1", "3", "30.5")


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
                with store.lock(ACTION_ID, timeout=0.01):
                    self.fail("same action lock must not be acquired concurrently")

    def test_action_lock_timeout_requires_finite_positive_duration(self):
        store = ActionStore(self.base / "state")
        for value in (float("nan"), float("inf"), float("-inf"), 0, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite positive duration"):
                    with store.lock(ACTION_ID, timeout=value):
                        self.fail("invalid lock timeout must fail before acquisition")

    def test_additive_v1_fields_are_backfilled_when_loading_old_evidence(self):
        payload = self.action().to_dict()
        del payload["registration"]["first_absence_observed_at"]
        del payload["registration"]["last_absence_observed_at"]
        del payload["local_observation"]["config_runner_id"]
        del payload["github_observation"]["ephemeral"]
        loaded = EphemeralAction.from_dict(payload)
        self.assertIsNone(loaded.registration["first_absence_observed_at"])
        self.assertIsNone(loaded.registration["last_absence_observed_at"])
        self.assertIsNone(loaded.local_observation["config_runner_id"])
        self.assertIsNone(loaded.github_observation["ephemeral"])

    def test_cleaned_state_cannot_transition_out(self):
        action = self.action()
        action.transition(LifecycleState.CLEANED, action.updated_at, "cleaned")
        action.transition(LifecycleState.CLEANED, action.updated_at, "still_cleaned")
        with self.assertRaisesRegex(ValueError, "convergent terminal"):
            action.transition(LifecycleState.ONLINE, action.updated_at, "regression")

    def test_public_cli_durations_require_finite_positive_values(self):
        base = ["create", "Example/Repo", "--profile", "generic", "--labels", "ephemeral"]
        for option in ("--online-timeout", "--observation-interval"):
            for value in INVALID_DURATIONS:
                with self.subTest(option=option, value=value):
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            build_parser().parse_args(base + [option, value])
                    self.assertEqual(raised.exception.code, 2)
            for value in VALID_DURATIONS:
                with self.subTest(option=option, value=value):
                    parsed = build_parser().parse_args(base + [option, value])
                    attribute = option[2:].replace("-", "_")
                    self.assertEqual(getattr(parsed, attribute), float(value))

    def test_environment_durations_require_finite_positive_values(self):
        names = (
            "RUNNER_EPHEMERAL_REGISTRATION_ABSENCE_CONFIRM_SECONDS",
            "RUNNER_EPHEMERAL_COMMAND_TIMEOUT_SECONDS",
            "RUNNER_EPHEMERAL_GITHUB_TIMEOUT_SECONDS",
        )
        valid_environment = {name: "1" for name in names}
        with patch.dict(os.environ, valid_environment, clear=False):
            for name in names:
                for value in INVALID_DURATIONS:
                    with self.subTest(name=name, value=value):
                        with patch.dict(os.environ, {name: value}, clear=False):
                            with self.assertRaisesRegex(
                                    ValueError, "finite positive duration"):
                                _runtime()
                for value in VALID_DURATIONS:
                    with self.subTest(name=name, value=value):
                        with patch.dict(os.environ, {name: value}, clear=False):
                            lifecycle = _runtime()
                        if name == "RUNNER_EPHEMERAL_COMMAND_TIMEOUT_SECONDS":
                            observed = lifecycle.local.command_timeout
                        elif name == "RUNNER_EPHEMERAL_GITHUB_TIMEOUT_SECONDS":
                            observed = lifecycle.github.timeout
                        else:
                            observed = lifecycle.registration_absence_confirm_seconds
                        self.assertEqual(observed, float(value))


if __name__ == "__main__":
    unittest.main()
