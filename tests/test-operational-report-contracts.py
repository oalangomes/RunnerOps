#!/usr/bin/env python3
import copy
from datetime import datetime, timezone
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from operational_report import build_report, duration  # noqa: E402


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def snapshot(canonical="Example/Project"):
    return {
        "schema_version": 1,
        "kind": "CapacitySnapshot",
        "observed_at": "2026-09-25T11:59:00+00:00",
        "status": "complete",
        "repository": {"requested": ".", "nameWithOwner": canonical, "match_key": "example/project"},
        "errors": [],
        "queue": {
            "status": "complete",
            "observed_queued_job_count": 2,
            "queued_job_count": 2,
            "jobs": [
                {"job_id": 2, "queue_age_seconds": 30},
                {"job_id": 1, "queue_age_seconds": 90},
            ],
        },
        "capacity": {"counts": {
            "available_now": 1, "busy_capacity": 2,
            "provisioned_idle": 0, "inconclusive": 0,
        }},
        "collector": {"wall_time_ms": 12, "github_calls": 4},
    }


class FakeStore:
    history_value = {
        "decisions": [
            {"decision": "PROVISION_LOCAL", "reason_codes": ["Z_REASON", "A_REASON"]},
            {"decision": "WAIT", "reason_codes": ["A_REASON"]},
            {"decision": "START_LOCAL", "reason_codes": ["CAPACITY_BUSY"]},
            {"decision": "INCONCLUSIVE", "reason_codes": ["EVIDENCE_INCONCLUSIVE"]},
        ],
        "actions": [
            {"kind": "START_LOCAL", "state": "succeeded", "diagnostic": {"code": None}},
            {"kind": "PROVISION_LOCAL", "state": "failed", "diagnostic": {"code": "START_FAILED"}},
            {"kind": "START_LOCAL", "state": "planned", "diagnostic": {"code": None}},
        ],
        "queue_observations": [{"end_reason": "inconclusive_observation"}, {"end_reason": "left_queue"}],
        "truncated": False,
    }

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def history(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return copy.deepcopy(self.history_value)


class ReportContracts(unittest.TestCase):
    def test_schema_period_and_aggregation_are_deterministic(self):
        stores = []

        def factory():
            store = FakeStore()
            stores.append(store)
            return store

        calls = []

        def collect(repository, persist_cache=True):
            calls.append((repository, persist_cache))
            return snapshot()

        first = build_report(".", 86400, now=NOW, snapshot_fn=collect, store_factory=factory)
        second = build_report(".", 86400, now=NOW, snapshot_fn=collect, store_factory=factory)
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["kind"], "OperationalEvidence")
        self.assertEqual(first["period"]["from"], "2026-09-24T12:00:00+00:00")
        self.assertEqual(first["period"]["to"], "2026-09-25T12:00:00+00:00")
        self.assertEqual(first["autoscale"]["decisions"]["by_kind"], {
            "INCONCLUSIVE": 1, "PROVISION_LOCAL": 1, "START_LOCAL": 1, "WAIT": 1,
        })
        self.assertEqual(first["autoscale"]["decisions"]["reason_codes"]["A_REASON"], 2)
        self.assertEqual(first["autoscale"]["actions"]["by_state"], {
            "failed": 1, "planned": 1, "succeeded": 1,
        })
        self.assertEqual(calls, [(".", False), (".", False)])
        self.assertEqual(stores[0].calls[0][1]["repository"], "Example/Project")

    def test_missing_history_and_canonical_identity_are_explicit(self):
        def collect(repository, persist_cache=True):
            return snapshot(None)

        result = build_report("owner/project", 60, now=NOW, snapshot_fn=collect,
                              store_factory=FakeStore)
        self.assertIsNone(result["repository"]["nameWithOwner"])
        self.assertIn({"source": "repository", "reason": "canonical_repository_unavailable"},
                      result["incomplete_evidence"])
        self.assertIn({"source": "autoscale", "reason": "audit_history_unavailable"},
                      result["incomplete_evidence"])
        self.assertIn({"source": "capacity", "reason": "historical_capacity_not_persisted"},
                      result["incomplete_evidence"])
        self.assertIsNone(result["capacity"]["historical_utilization"])

    def test_duration_is_positive_and_bounded(self):
        self.assertEqual(duration("24h"), 86400)
        with self.assertRaises(Exception):
            duration("0h")
        with self.assertRaises(Exception):
            duration("3651d")

    def test_json_is_one_document_without_secret_fields(self):
        result = build_report(".", 3600, now=NOW, snapshot_fn=lambda *args, **kwargs: snapshot(),
                              store_factory=FakeStore)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.assertEqual(json.loads(encoded), result)
        self.assertNotIn("credential", encoded.lower())
        self.assertNotIn("token", encoded.lower())


if __name__ == "__main__":
    unittest.main()
