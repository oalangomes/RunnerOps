#!/usr/bin/env python3
"""Governed ephemeral planner, replay and scheduler-tick contracts."""

import sys
import unittest
import importlib.util
from unittest.mock import patch
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
def fixture_module(name):
    path = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller_fixture = fixture_module("test-autoscale-controller-contracts.py")
planner_fixture = fixture_module("test-autoscale-provision-planner-contracts.py")
from runnerops.autoscale.ephemeral import exact_ephemeral_id, load_ephemeral_policy  # noqa: E402
from runnerops.autoscale.ephemeral_controller import planned_action, reconcile_or_apply  # noqa: E402
from runnerops.autoscale.planner import plan, policy_fingerprint  # noqa: E402
from runnerops.autoscale.runtime import decision_from_plan, read_planner_evidence  # noqa: E402
from runnerops.ephemeral.identity import runner_identity  # noqa: E402


LABELS = ["self-hosted", "Linux", "X64", "runnerops"]


def ephemeral_policy(**overrides):
    result = {"enabled": True, "max_active": 2, "cooldown_seconds": 30,
              "profile": "generic", "labels": LABELS}
    result.update(overrides)
    return result


class PlannerContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = planner_fixture.ProvisionPlannerContracts()

    def case(self, *, provision_enabled=False, ephemeral=None, actions=None, job=None,
             snapshot=None, max_active=1):
        policy = self.fixture.policy(enabled=provision_enabled)
        policy["local_ephemeral"] = ephemeral if ephemeral is not None else ephemeral_policy()
        policy["max_active_local_runners"] = max_active
        job = job or self.fixture.job()
        audit = self.fixture.audit(job)
        audit["ephemeral"] = {"status": "complete", "actions": actions or []}
        return plan(snapshot or self.fixture.snapshot(job=job), policy,
                    self.fixture.host(), audit)

    def test_available_and_reusable_capacity_precede_ephemeral(self):
        idle = self.fixture.local_runner("idle", category="provisioned_idle")
        job = self.fixture.job(status="provisioned_idle", names=["idle"])
        result = self.case(job=job, snapshot=self.fixture.snapshot(
            local_rows=[idle], active_local=0, job=job))
        self.assertEqual(result["decision"], "START_LOCAL")
        available = self.fixture.local_runner("available", category="available_now")
        job = self.fixture.job(status="available_now", names=["available"])
        result = self.case(job=job, snapshot=self.fixture.snapshot(
            local_rows=[available], active_local=1, job=job))
        self.assertEqual(result["decision"], "WAIT")

    def test_provision_precedes_ephemeral(self):
        result = self.case(provision_enabled=True, max_active=2)
        self.assertEqual(result["decision"], "PROVISION_LOCAL")

    def test_exhausted_persistent_options_select_exact_ephemeral(self):
        result = self.case()
        self.assertEqual(result["decision"], "CREATE_EPHEMERAL")
        self.assertEqual(result["action"]["target"], exact_ephemeral_id(result["decision_id"]))
        self.assertEqual(len(result["action"]["target"]), 32)
        self.assertIn("EPHEMERAL_CAPACITY_LIMIT_AVAILABLE", result["reason_codes"])

    def test_disabled_limit_inconclusive_and_labels_fail_closed(self):
        self.assertNotEqual(self.case(ephemeral=ephemeral_policy(enabled=False))["decision"],
                            "CREATE_EPHEMERAL")
        rows = [{"action_id": "a" * 32, "state": "ONLINE", "labels": LABELS,
                 "updated_at": self.fixture.observed_at}]
        self.assertEqual(self.case(ephemeral=ephemeral_policy(max_active=1), actions=rows)["decision"],
                         "HOLD")
        self.assertEqual(self.case(actions=[{**rows[0], "state": "INCONCLUSIVE_TERMINAL"}])["decision"],
                         "HOLD")
        incompatible = ephemeral_policy(labels=["self-hosted", "Linux"])
        self.assertNotEqual(self.case(ephemeral=incompatible)["decision"], "CREATE_EPHEMERAL")
        policy = self.fixture.policy(enabled=False)
        policy["local_ephemeral"] = ephemeral_policy()
        policy["max_active_local_runners"] = 1
        audit = self.fixture.audit()
        audit["ephemeral"] = {"status": "inconclusive", "actions": []}
        self.assertEqual(plan(self.fixture.snapshot(), policy, self.fixture.host(), audit)["decision"],
                         "INCONCLUSIVE")

    def test_policy_participates_in_fingerprint(self):
        first = self.fixture.policy(enabled=False)
        first["local_ephemeral"] = ephemeral_policy()
        second = deepcopy(first)
        second["local_ephemeral"]["max_active"] = 3
        self.assertNotEqual(policy_fingerprint(first), policy_fingerprint(second))

    def test_busy_ephemeral_allows_one_more_with_headroom_but_online_covers_queue(self):
        row = {"action_id": "a" * 32, "state": "BUSY", "labels": LABELS,
               "updated_at": self.fixture.observed_at}
        self.assertEqual(self.case(actions=[row])["decision"], "CREATE_EPHEMERAL")
        self.assertEqual(self.case(actions=[{**row, "state": "ONLINE"}])["decision"], "HOLD")
        self.assertEqual(self.case(actions=[{**row, "updated_at": "2026-09-17T12:00:00+00:00"}])[
            "decision"], "HOLD")

    def test_invalid_policy_fails_early(self):
        with patch.dict("os.environ", {"RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_ENABLED": "true",
                                     "RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_EPHEMERALS": "0",
                                     "RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_PROFILE": "generic",
                                     "RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_LABELS": "self-hosted,Linux,X64"}):
            with self.assertRaises(ValueError):
                load_ephemeral_policy()


class FakeAction:
    def __init__(self, action_id, repository, profile, labels, state="ONLINE"):
        self.action_id = action_id
        self.runner_identity = runner_identity(action_id)
        self.repository = repository
        self.profile = profile
        self.labels = labels + [self.runner_identity]
        self.action_state = state
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.registration = {"attempted": state != "REQUESTED", "safe_retry_authorized": False}


class FakeLifecycle:
    def __init__(self):
        self.actions = {}
        self.store = self
        self.creates = 0
        self.reconciles = 0
        self.cleanups = 0

    def exists(self, action_id):
        return action_id in self.actions

    def load(self, action_id):
        return self.actions[action_id]

    def create(self, repository, profile, labels, *, action_id):
        self.creates += 1
        if action_id in self.actions:
            action = self.actions[action_id]
            assert action.action_state == "REQUESTED" and not action.registration["attempted"]
            action.action_state = "ONLINE"
            return action
        action = FakeAction(action_id, repository, profile, labels)
        self.actions[action_id] = action
        return action

    def reconcile(self, action_id):
        self.reconciles += 1
        return self.actions[action_id]

    def cleanup(self, action_id):
        self.cleanups += 1
        self.actions[action_id].action_state = "CLEANED"
        return self.actions[action_id]

    def evidence(self, repository):
        return {"status": "complete", "actions": [
            {"action_id": row.action_id, "state": row.action_state,
             "labels": row.labels, "updated_at": row.updated_at}
            for row in self.actions.values()
            if row.repository.casefold() == repository.casefold() and row.action_state != "CLEANED"
        ]}


class ControllerContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = controller_fixture.ControllerContracts()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.lifecycle = FakeLifecycle()
        self.policy = self.fixture.policy(
            max_active_local_runners=1,
            local_provision={"enabled": False, "max_local_runners": 0,
                             "template": {"profile": None, "group": None,
                                          "labels": [], "name_prefix": None,
                                          "runner_version": "latest", "runner_arch": "auto"}},
            local_ephemeral=ephemeral_policy(max_active=1),
        )
        self.fixture.seed_queue(category="busy_capacity", names=("busy",), active_local=1)

    def run_tick(self):
        return self.fixture.run_controller(
            [self.fixture.snapshot(category="busy_capacity", names=("busy",), active_local=1,
                                   observed_at=self.fixture.now)],
            policy_loader=lambda: self.policy,
            ephemeral_lifecycle_factory=lambda: self.lifecycle,
            ephemeral_evidence_fn=self.lifecycle.evidence,
        )

    def test_exact_action_replay_and_cleanup_converge(self):
        result, code = self.run_tick()
        self.assertEqual(code, 0)
        self.assertEqual(result["decision"], "CREATE_EPHEMERAL")
        self.assertEqual(result["action_state"], "started")
        self.assertEqual(self.lifecycle.creates, 1)
        exact_id = result["ephemeral_action_id"]
        self.assertEqual(result["target"], exact_id)
        with self.fixture.store_factory() as store:
            explained = store.explain(result["decision_id"])
        self.assertEqual(explained["actions"][0]["target"], exact_id)
        self.assertEqual([event["state"] for event in explained["actions"][0]["events"]],
                         ["planned", "started"])
        self.fixture.now = self.fixture.now.replace(microsecond=1000)
        result, code = self.run_tick()
        self.assertEqual(self.lifecycle.creates, 1)
        self.assertGreaterEqual(self.lifecycle.reconciles, 1)
        self.lifecycle.actions[exact_id].action_state = "TERMINAL"
        self.fixture.now = self.fixture.now.replace(microsecond=2000)
        result, code = self.run_tick()
        self.assertEqual(code, 0)
        self.assertEqual(self.lifecycle.cleanups, 1)
        self.assertEqual(self.lifecycle.actions[exact_id].action_state, "CLEANED")
        self.assertEqual(self.lifecycle.creates, 1)
        with self.fixture.store_factory() as store:
            action = store.explain(result["decision_id"])["actions"][0]
        self.assertEqual(action["state"], "succeeded")

    def test_inconclusive_registration_never_creates_another(self):
        result, _ = self.run_tick()
        exact_id = result["ephemeral_action_id"]
        self.lifecycle.actions[exact_id].action_state = "INCONCLUSIVE_REGISTRATION"
        self.fixture.now = self.fixture.now.replace(microsecond=1000)
        result, _ = self.run_tick()
        self.assertEqual(self.lifecycle.creates, 1)
        self.assertEqual(result["decision"], "CREATE_EPHEMERAL")

    def test_inconclusive_terminal_blocks_duplicate_and_cleanup(self):
        result, _ = self.run_tick()
        exact_id = result["ephemeral_action_id"]
        self.lifecycle.actions[exact_id].action_state = "INCONCLUSIVE_TERMINAL"
        self.fixture.now = self.fixture.now.replace(microsecond=1000)
        result, code = self.run_tick()
        self.assertEqual(code, 3)
        self.assertEqual(self.lifecycle.creates, 1)
        self.assertEqual(self.lifecycle.cleanups, 0)
        self.assertEqual(result["ephemeral_action_id"], exact_id)

    def test_crash_after_lifecycle_persist_reconciles_same_planned_action(self):
        snapshot = self.fixture.snapshot(category="busy_capacity", names=("busy",),
                                         active_local=1, observed_at=self.fixture.now)
        with self.fixture.store_factory() as store:
            store.observe(snapshot)
            audit = read_planner_evidence(store, "Example/RunnerOps")
            audit["ephemeral"] = self.lifecycle.evidence("Example/RunnerOps")
            planned = plan(snapshot, self.policy,
                           {"status": "complete", "memory_available_mib": 8192,
                            "cpu_percent": None}, audit)
            self.assertEqual(planned["decision"], "CREATE_EPHEMERAL")
            store.record_decision(decision_from_plan(planned))
            action = planned_action(planned, self.fixture.now.isoformat())
            store.record_action(action)
        exact_id = action["target"]
        self.lifecycle.actions[exact_id] = FakeAction(exact_id, "Example/RunnerOps",
                                                       "generic", LABELS, "REGISTERING")
        result, code = self.run_tick()
        self.assertEqual(code, 0)
        self.assertEqual(result["ephemeral_action_id"], exact_id)
        self.assertEqual(self.lifecycle.creates, 0)
        self.assertEqual(self.lifecycle.reconciles, 1)
        self.lifecycle.actions[exact_id].action_state = "INCONCLUSIVE_REGISTRATION"
        self.fixture.now = self.fixture.now.replace(microsecond=1000)
        result, code = self.run_tick()
        self.assertEqual(code, 3)
        self.assertEqual(self.lifecycle.creates, 0)
        self.assertEqual(result["ephemeral_action_id"], exact_id)

    def test_crash_before_registration_resumes_only_exact_requested_identity(self):
        snapshot = self.fixture.snapshot(category="busy_capacity", names=("busy",),
                                         active_local=1, observed_at=self.fixture.now)
        with self.fixture.store_factory() as store:
            store.observe(snapshot)
            audit = read_planner_evidence(store, "Example/RunnerOps")
            audit["ephemeral"] = self.lifecycle.evidence("Example/RunnerOps")
            planned = plan(snapshot, self.policy,
                           {"status": "complete", "memory_available_mib": 8192,
                            "cpu_percent": None}, audit)
            store.record_decision(decision_from_plan(planned))
            action = planned_action(planned, self.fixture.now.isoformat())
            store.record_action(action)
        exact_id = action["target"]
        self.lifecycle.actions[exact_id] = FakeAction(exact_id, "Example/RunnerOps",
                                                       "generic", LABELS, "REQUESTED")
        result, code = self.run_tick()
        self.assertEqual(code, 0)
        self.assertEqual(result["target"], exact_id)
        self.assertEqual(self.lifecycle.creates, 1)
        self.assertEqual(len(self.lifecycle.actions), 1)


if __name__ == "__main__":
    unittest.main()
