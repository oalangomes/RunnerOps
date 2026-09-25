#!/usr/bin/env python3
import copy
import ast
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import capacity  # noqa: E402
from autoscale_contracts import AuditError  # noqa: E402
from autoscale_store import AuditStore  # noqa: E402
from operational_report import build_report, duration, main  # noqa: E402


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

        def collect(repository):
            calls.append(repository)
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
        self.assertEqual(calls, [".", "."])
        self.assertEqual(tuple(inspect.signature(capacity.snapshot).parameters), ("requested",))
        self.assertEqual(stores[0].calls[0][1]["repository"], "Example/Project")
        self.assertEqual(stores[0].calls[0][0][0], first["period"]["from"])
        self.assertEqual(stores[0].calls[0][1]["until"], first["period"]["to"])
        self.assertEqual(first["collection_status"], "success")
        self.assertEqual({item["reason"] for item in first["incomplete_evidence"]}, {
            "ci_history_not_persisted", "historical_capacity_not_persisted",
            "historical_collector_metrics_unavailable",
        })

    def test_missing_history_and_canonical_identity_are_explicit(self):
        def collect(repository):
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

    def test_truncated_history_is_explicit_runtime_incomplete_evidence(self):
        class TruncatedStore(FakeStore):
            def history(self, *args, **kwargs):
                result = super().history(*args, **kwargs)
                result["truncated"] = True
                return result

        result = build_report(".", 3600, now=NOW, snapshot_fn=lambda repository: snapshot(),
                              store_factory=TruncatedStore)
        self.assertIn({"source": "autoscale", "reason": "audit_history_truncated"},
                      result["incomplete_evidence"])
        self.assertEqual(result["collection_status"], "failed")

    def test_runtime_snapshot_and_history_failures_are_explicit(self):
        def unavailable_snapshot(repository):
            raise ValueError("query failed")

        unavailable = build_report(".", 3600, now=NOW, snapshot_fn=unavailable_snapshot,
                                   store_factory=FakeStore)
        self.assertIn({"source": "capacity", "reason": "current_capacity_unavailable"},
                      unavailable["incomplete_evidence"])
        self.assertEqual(unavailable["collection_status"], "failed")

        class FailedQueryStore(FakeStore):
            def history(self, *args, **kwargs):
                raise AuditError("store_corrupt")

        failed_query = build_report(".", 3600, now=NOW, snapshot_fn=lambda repository: snapshot(),
                                    store_factory=FailedQueryStore)
        self.assertIn({"source": "autoscale", "reason": "query_failed"},
                      failed_query["incomplete_evidence"])
        self.assertEqual(failed_query["collection_status"], "failed")
        with patch("operational_report.build_report", return_value=failed_query), \
                patch("sys.argv", ["operational_report.py", "--since", "1h"]), \
                redirect_stdout(StringIO()):
            self.assertEqual(main(), 3)

        inconclusive_snapshot = snapshot()
        inconclusive_snapshot["status"] = "inconclusive"
        inconclusive = build_report(
            ".", 3600, now=NOW, snapshot_fn=lambda repository: inconclusive_snapshot,
            store_factory=FakeStore,
        )
        self.assertIn({"source": "capacity", "reason": "current_capacity_inconclusive"},
                      inconclusive["incomplete_evidence"])
        self.assertEqual(inconclusive["collection_status"], "failed")

    def test_report_reads_audit_store_without_mutating_it_or_calling_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "autoscale.db"
            with AuditStore(path, writable=True, clock=lambda: NOW):
                pass
            before = path.read_bytes()
            calls = []

            def collect(repository):
                calls.append(repository)
                return snapshot()

            result = build_report(".", 3600, now=NOW, snapshot_fn=collect,
                                  store_factory=lambda: AuditStore(path, clock=lambda: NOW))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(calls, ["."])
            self.assertEqual(result["collection_status"], "success")

    def test_report_has_no_mutating_or_provisioning_dependencies(self):
        tree = ast.parse((ROOT / "operational_report.py").read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute)
            else node.func.id if isinstance(node.func, ast.Name)
            else None
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        self.assertFalse(imported & {
            "autoscale_controller", "autoscale_planner", "autoscale_provision",
            "autoscale_provision_controller",
        })
        self.assertFalse(called & {
            "observe", "record_decision", "record_action", "run_once", "provision",
        })

    def test_capacity_source_has_no_persistent_job_cache(self):
        source = (ROOT / "capacity.py").read_text()
        for forbidden in (
            "_queue_cache_path", "_load_queue_cache", "_save_queue_cache",
            "QUEUE_CACHE_VERSION", "job_cache_hits", "job_cache_misses",
            "job_cache_invalidations", "workflow_run.updated_at",
        ):
            self.assertNotIn(forbidden, source)

    def test_main_emits_one_json_document_and_returns_collection_status(self):
        result = build_report(".", 3600, now=NOW, snapshot_fn=lambda repository: snapshot(),
                              store_factory=FakeStore)
        output = StringIO()
        with patch("operational_report.build_report", return_value=result), \
                patch("sys.argv", ["operational_report.py", "--since", "1h", "--json"]), \
                redirect_stdout(output):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue()), result)
        self.assertEqual(output.getvalue().count("\n"), 1)

    def test_json_is_one_document_without_secret_fields(self):
        evidence = snapshot()
        evidence["collector"].update({"token": "SECRET_TOKEN", "raw_logs": "SECRET_LOG"})
        result = build_report(".", 3600, now=NOW, snapshot_fn=lambda repository: evidence,
                              store_factory=FakeStore)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.assertEqual(json.loads(encoded), result)
        self.assertNotIn("credential", encoded.lower())
        self.assertNotIn("token", encoded.lower())
        self.assertNotIn("raw_logs", encoded.lower())
        self.assertNotIn("secret_log", encoded.lower())


if __name__ == "__main__":
    unittest.main()
