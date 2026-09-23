#!/usr/bin/env python3
"""Replay/no-blind-retry contracts for the #73 provision state machine."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_planner import policy_fingerprint  # noqa: E402
from autoscale_provision_controller import (  # noqa: E402
    planned_action,
    reconcile_or_apply,
)


class MemoryStore:
    def __init__(self):
        self.actions = []

    def record_action(self, action):
        self.actions.append(action)
        return True


class ProvisionControllerContracts(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
        self.store = MemoryStore()

    def clock(self):
        return self.now

    def advance(self, seconds=1):
        self.now += timedelta(seconds=seconds)

    def policy(self, *, prefix="example-auto"):
        return {
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
                    "group": "example",
                    "labels": ["Linux", "X64", "local-runner", "python", "self-hosted"],
                    "name_prefix": prefix,
                    "runner_version": "2.999.0",
                    "runner_arch": "x64",
                },
            },
        }

    def plan(self, policy=None, target="example-auto-02"):
        policy = policy or self.policy()
        return {
            "decision_id": "plan-provision-1",
            "timestamp": self.now.isoformat(),
            "repository": "Example/Project",
            "policy_fingerprint": policy_fingerprint(policy),
            "decision": "PROVISION_LOCAL",
            "reason_codes": ["LOCAL_CAPACITY_DEFICIT", "LOCAL_POOL_BELOW_MAX"],
            "requested_capacity_delta": 3,
            "action": {"kind": "PROVISION_LOCAL", "target": target},
        }

    def decision(self, plan):
        return {
            "decision_id": plan["decision_id"],
            "timestamp": plan["timestamp"],
            "repository": plan["repository"],
            "policy_fingerprint": plan["policy_fingerprint"],
            "decision": plan["decision"],
            "reason_codes": plan["reason_codes"],
            "requested_capacity_delta": plan["requested_capacity_delta"],
        }

    def snapshot(self, target=None, *, ready=False, present=False):
        rows = []
        if target is not None and (ready or present):
            rows.append(
                {
                    "name": target,
                    "registration_id": 42,
                    "scope": "local",
                    "enabled": True,
                    "local": {"state": "healthy_idle" if ready else "unknown"},
                    "github": {
                        "id": 42,
                        "status": "offline" if ready else "unknown",
                        "busy": False if ready else None,
                    },
                    "category": "provisioned_idle" if ready else "inconclusive",
                }
            )
        return {"sources": {"local": "complete"}, "capacity": {"runners": rows}}

    def pending(self, plan, action):
        return {"decision": self.decision(plan), "action": action}

    def test_planned_action_has_immutable_exact_target_and_provision_kind(self):
        plan = self.plan()
        action = planned_action(plan, self.now.isoformat())
        self.assertEqual(action["kind"], "PROVISION_LOCAL")
        self.assertEqual(action["target"], "example-auto-02")
        self.assertEqual(action["state"], "planned")
        self.assertTrue(action["action_id"].startswith("action-"))

    def test_inconclusive_add_crosses_boundary_once_then_never_retries_blindly(self):
        policy = self.policy()
        plan = self.plan(policy)
        action = planned_action(plan, self.now.isoformat())
        calls = []

        def provision(repo, target, provision_policy):
            calls.append((repo, target, provision_policy))
            return {"status": "inconclusive", "code": "PROVISION_PARTIAL", "exit_code": 1}

        result, code = reconcile_or_apply(
            self.store,
            self.pending(plan, action),
            self.snapshot(),
            plan,
            policy,
            "Example/Project",
            provision_fn=provision,
            snapshot_fn=lambda repo: self.snapshot(),
            clock=self.clock,
        )
        self.assertEqual(code, 3)
        self.assertEqual(result["diagnostic"], "PROVISION_PARTIAL")
        self.assertEqual(len(calls), 1)
        started = self.store.actions[-1]
        self.assertEqual(started["state"], "started")

        self.advance()
        result, code = reconcile_or_apply(
            self.store,
            self.pending(plan, started),
            self.snapshot(),
            plan,
            policy,
            "Example/Project",
            provision_fn=lambda *args: self.fail("started action must never call add again"),
            snapshot_fn=lambda repo: self.snapshot(),
            clock=self.clock,
        )
        self.assertEqual(code, 3)
        self.assertEqual(result["diagnostic"], "PROVISION_OUTCOME_UNKNOWN")
        self.assertEqual(len(calls), 1)

    def test_started_action_reconciles_exact_target_to_success(self):
        policy = self.policy()
        plan = self.plan(policy)
        action = planned_action(plan, self.now.isoformat())
        self.advance()
        started = {
            **action,
            "state": "started",
            "timestamp": self.now.isoformat(),
            "started_at": self.now.isoformat(),
            "diagnostic": {"code": "PROVISION_REQUESTED", "exit_code": None},
        }
        self.advance()
        result, code = reconcile_or_apply(
            self.store,
            self.pending(plan, started),
            self.snapshot(action["target"], ready=True),
            plan,
            policy,
            "Example/Project",
            provision_fn=lambda *args: self.fail("recovery must not call add"),
            snapshot_fn=lambda repo: self.snapshot(action["target"], ready=True),
            clock=self.clock,
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["diagnostic"], "PROVISION_VERIFIED_IDLE")
        terminal = self.store.actions[-1]
        self.assertEqual(terminal["state"], "succeeded")
        self.assertEqual(terminal["external_id"], "42")

    def test_present_but_unverified_target_stays_inconclusive_without_retry(self):
        policy = self.policy()
        plan = self.plan(policy)
        action = planned_action(plan, self.now.isoformat())
        self.advance()
        started = {
            **action,
            "state": "started",
            "timestamp": self.now.isoformat(),
            "started_at": self.now.isoformat(),
            "diagnostic": {"code": "PROVISION_REQUESTED", "exit_code": None},
        }
        result, code = reconcile_or_apply(
            self.store,
            self.pending(plan, started),
            self.snapshot(action["target"], present=True),
            plan,
            policy,
            "Example/Project",
            provision_fn=lambda *args: self.fail("uncertain recovery must not call add"),
            snapshot_fn=lambda repo: self.snapshot(),
            clock=self.clock,
        )
        self.assertEqual(code, 3)
        self.assertEqual(result["diagnostic"], "PROVISION_TARGET_PRESENT_UNVERIFIED")
        self.assertEqual(self.store.actions, [])

    def test_policy_change_cancels_only_planned_action_before_mutation(self):
        policy = self.policy()
        plan = self.plan(policy)
        action = planned_action(plan, self.now.isoformat())
        changed = self.policy(prefix="different")
        self.advance()
        result, code = reconcile_or_apply(
            self.store,
            self.pending(plan, action),
            self.snapshot(),
            self.plan(changed, target="different-01"),
            changed,
            "Example/Project",
            provision_fn=lambda *args: self.fail("changed planned action must not mutate"),
            snapshot_fn=lambda repo: self.snapshot(),
            clock=self.clock,
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["diagnostic"], "POLICY_CHANGED")
        self.assertEqual(self.store.actions[-1]["state"], "cancelled")

    def test_requested_delta_does_not_create_more_than_one_runner(self):
        policy = self.policy()
        plan = self.plan(policy)
        self.assertEqual(plan["requested_capacity_delta"], 3)
        action = planned_action(plan, self.now.isoformat())
        calls = []

        def provision(*args):
            calls.append(args)
            return {"status": "ok", "code": "PROVISION_ADD_COMPLETED", "exit_code": 0}

        result, code = reconcile_or_apply(
            self.store,
            self.pending(plan, action),
            self.snapshot(),
            plan,
            policy,
            "Example/Project",
            provision_fn=provision,
            snapshot_fn=lambda repo: self.snapshot(action["target"], ready=True),
            clock=self.clock,
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["action_state"], "succeeded")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
