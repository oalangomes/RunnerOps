#!/usr/bin/env python3
"""Focused collector performance contracts for issue #105."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


class CapacityCollectorPerformanceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("capacity_perf", ROOT / "capacity.py")
        cls.capacity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.capacity)

    def setUp(self):
        self.capacity.reset_process_cache()

    def test_successful_repository_resolution_is_reused_process_locally(self):
        calls = []

        def fake_command(*args):
            calls.append(args)
            self.assertEqual(args, ("git", "remote", "get-url", "origin"))
            return "git@github.com:Example/MixedCase.git"

        with patch.object(self.capacity, "command", side_effect=fake_command):
            first = self.capacity.collector_metrics()
            second = self.capacity.collector_metrics()
            alias = self.capacity.collector_metrics()
            self.assertEqual(
                self.capacity.resolve_repo(".", metrics=first),
                ("Example/MixedCase", "example/mixedcase"),
            )
            self.assertEqual(
                self.capacity.resolve_repo(".", metrics=second),
                ("Example/MixedCase", "example/mixedcase"),
            )
            self.assertEqual(
                self.capacity.resolve_repo("Example/MixedCase", metrics=alias),
                ("Example/MixedCase", "example/mixedcase"),
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(first["canonical_source"], "git_remote")
        self.assertEqual(first["repo_resolution_calls"], 0)
        self.assertEqual(first["github_calls"], 0)
        self.assertEqual(second["repo_cache_hits"], 1)
        self.assertEqual(second["github_calls"], 0)
        self.assertEqual(alias["repo_cache_hits"], 1)
        self.assertEqual(alias["canonical_source"], "process_cache")

    def test_dot_repository_cache_is_scoped_to_working_directory(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            previous = os.getcwd()
            calls = []

            def fake_command(*args):
                calls.append(args)
                self.assertEqual(args, ("git", "remote", "get-url", "origin"))
                return "git@github.com:Example/MixedCase.git"

            try:
                with patch.object(self.capacity, "command", side_effect=fake_command):
                    os.chdir(first_dir)
                    self.capacity.resolve_repo(".", metrics=self.capacity.collector_metrics())
                    self.capacity.resolve_repo(".", metrics=self.capacity.collector_metrics())
                    os.chdir(second_dir)
                    self.capacity.resolve_repo(".", metrics=self.capacity.collector_metrics())
            finally:
                os.chdir(previous)

        self.assertEqual(len(calls), 2)

    def test_failed_repository_resolution_is_not_cached(self):
        calls = []

        def fail(*args):
            calls.append(args)
            raise self.capacity.EvidenceError("query_failed")

        with patch.object(self.capacity, "command", side_effect=fail):
            for _ in range(2):
                metrics = self.capacity.collector_metrics()
                self.assertEqual(self.capacity.resolve_repo(".", metrics=metrics), (None, None))
                self.assertEqual(metrics["repo_cache_hits"], 0)
                self.assertEqual(metrics["repo_resolution_calls"], 1)

        # Each attempt performs gh repo view and then the safe git-origin fallback.
        self.assertEqual(len(calls), 4)

    def test_api_page_metrics_count_actual_github_requests(self):
        def fake_command(*args):
            endpoint = args[-1]
            page = int(endpoint.rsplit("page=", 1)[1])
            rows = [{"id": number} for number in range(100)] if page == 1 else [{"id": 101}]
            return json.dumps({"workflow_runs": rows, "total_count": 101})

        errors = []
        metrics = self.capacity.collector_metrics()
        with patch.object(self.capacity, "command", side_effect=fake_command):
            rows = self.capacity.api_pages(
                "repos/Example/MixedCase/actions/runs?status=queued",
                "workflow_runs",
                errors,
                "queue",
                max_pages=10,
                metrics=metrics,
                metric_kind="run_list_calls",
            )

        self.assertEqual(len(rows), 101)
        self.assertEqual(errors, [])
        self.assertEqual(metrics["github_calls"], 2)
        self.assertEqual(metrics["run_list_calls"], 2)

    def test_queue_metrics_expose_status_and_per_run_job_amplification(self):
        run = {
            "id": 10,
            "workflow_id": 7,
            "name": "Build",
            "status": "in_progress",
            "run_attempt": 1,
            "head_sha": "abc123",
            "head_branch": "feature",
        }
        job = {
            "id": 101,
            "name": "test",
            "status": "queued",
            "created_at": "2026-09-17T12:00:00Z",
            "labels": ["self-hosted", "linux"],
        }

        def fake_command(*args):
            endpoint = args[-1]
            if "/jobs?" in endpoint:
                return json.dumps({"jobs": [job], "total_count": 1})
            rows = [run] if "status=in_progress" in endpoint else []
            return json.dumps({"workflow_runs": rows, "total_count": len(rows)})

        metrics = self.capacity.collector_metrics()
        errors = []
        with patch.object(self.capacity, "command", side_effect=fake_command):
            jobs = self.capacity.collect_queue(
                "Example/MixedCase",
                datetime(2026, 9, 17, 13, tzinfo=timezone.utc),
                errors,
                metrics,
            )

        self.assertEqual(errors, [])
        self.assertEqual([row["job_id"] for row in jobs], [101])
        self.assertEqual(metrics["run_list_calls"], len(self.capacity.RUN_STATUSES))
        self.assertEqual(metrics["job_list_calls"], 1)
        self.assertEqual(
            metrics["github_calls"], len(self.capacity.RUN_STATUSES) + 1
        )

    def test_repeated_queue_observation_reuses_unchanged_jobs(self):
        with tempfile.TemporaryDirectory() as cache_root:
            runs = [{
                "id": number,
                "workflow_id": 7,
                "name": "Build",
                "status": "queued",
                "run_attempt": 1,
                "head_sha": "abc123",
                "updated_at": "2026-09-24T12:00:00Z",
            } for number in range(1, 41)]
            calls = []

            def fake_api_pages(endpoint, field, errors, source, max_pages=100, *,
                               metrics=None, metric_kind=None, retries=0):
                calls.append(endpoint)
                self.assertIsNotNone(metrics)
                self.capacity.record_github_call(metrics, metric_kind)
                if field == "workflow_runs":
                    return list(runs) if endpoint.endswith("status=queued") else []
                run_id = int(endpoint.split("/runs/")[1].split("/")[0])
                return [{
                    "id": 1000 + run_id,
                    "name": "test",
                    "status": "queued",
                    "created_at": "2026-09-24T11:59:00Z",
                    "labels": ["self-hosted", "linux"],
                }]

            with patch.dict(os.environ, {"RUNNER_CACHE_ROOT": cache_root}):
                with patch.object(self.capacity, "api_pages", side_effect=fake_api_pages):
                    first_metrics = self.capacity.collector_metrics()
                    first_errors = []
                    first = self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), first_errors,
                        first_metrics, allow_persistent_cache=True,
                    )
                    second_metrics = self.capacity.collector_metrics()
                    second_errors = []
                    second = self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), second_errors,
                        second_metrics, allow_persistent_cache=True,
                    )

            self.assertEqual(len(first), 40)
            self.assertEqual(len(second), 40)
            self.assertEqual(first_errors, [])
            self.assertEqual(second_errors, [])
            self.assertEqual(first_metrics["job_list_calls"], 40)
            self.assertEqual(second_metrics["job_list_calls"], 0)
            self.assertEqual(second_metrics["job_cache_hits"], 40)
            self.assertEqual(len(calls), len(self.capacity.RUN_STATUSES) * 2 + 40)

    def test_changed_or_new_runs_are_refreshed_and_removed_runs_reconciled(self):
        with tempfile.TemporaryDirectory() as cache_root:
            runs = [{
                "id": 10, "workflow_id": 7, "name": "Build", "status": "queued",
                "run_attempt": 1, "head_sha": "abc123",
                "updated_at": "2026-09-24T12:00:00Z",
            }]
            jobs = {10: [{
                "id": 101, "name": "old", "status": "queued",
                "created_at": "2026-09-24T11:59:00Z", "labels": ["self-hosted"],
            }]}
            def fake_api_pages(endpoint, field, errors, source, max_pages=100, *,
                               metrics=None, metric_kind=None, retries=0):
                self.capacity.record_github_call(metrics, metric_kind)
                if field == "workflow_runs":
                    return list(runs) if endpoint.endswith("status=queued") else []
                run_id = int(endpoint.split("/runs/")[1].split("/")[0])
                return list(jobs[run_id])

            with patch.dict(os.environ, {"RUNNER_CACHE_ROOT": cache_root}):
                with patch.object(self.capacity, "api_pages", side_effect=fake_api_pages):
                    first_errors = []
                    self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), first_errors,
                        self.capacity.collector_metrics(), allow_persistent_cache=True,
                    )
                    runs[:] = [{
                        "id": 11, "workflow_id": 7, "name": "Build", "status": "queued",
                        "run_attempt": 1, "head_sha": "def456",
                        "updated_at": "2026-09-24T12:01:00Z",
                    }]
                    jobs[11] = [{
                        "id": 102, "name": "new", "status": "queued",
                        "created_at": "2026-09-24T12:01:00Z", "labels": ["self-hosted"],
                    }]
                    metrics = self.capacity.collector_metrics()
                    errors = []
                    result = self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), errors, metrics,
                        allow_persistent_cache=True,
                    )

            self.assertEqual(errors, [])
            self.assertEqual([job["job_id"] for job in result], [102])
            self.assertEqual(metrics["job_cache_hits"], 0)
            self.assertEqual(metrics["job_list_calls"], 1)

    def test_queue_failure_never_uses_cached_jobs(self):
        with tempfile.TemporaryDirectory() as cache_root:
            run = {
                "id": 10, "workflow_id": 7, "name": "Build", "status": "queued",
                "run_attempt": 1, "head_sha": "abc123",
                "updated_at": "2026-09-24T12:00:00Z",
            }

            def successful(endpoint, field, errors, source, max_pages=100, *,
                           metrics=None, metric_kind=None, retries=0):
                self.capacity.record_github_call(metrics, metric_kind)
                if field == "workflow_runs":
                    return [run] if endpoint.endswith("status=queued") else []
                return [{"id": 101, "name": "test", "status": "queued",
                         "created_at": "2026-09-24T11:59:00Z", "labels": ["self-hosted"]}]

            with patch.dict(os.environ, {"RUNNER_CACHE_ROOT": cache_root}):
                with patch.object(self.capacity, "api_pages", side_effect=successful):
                    self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), [],
                        self.capacity.collector_metrics(), allow_persistent_cache=True,
                    )

                def failed(endpoint, field, errors, source, max_pages=100, *,
                           metrics=None, metric_kind=None, retries=0):
                    self.capacity.record_github_call(metrics, metric_kind)
                    errors.append({"source": "queue", "reason": "query_failed"})
                    return []

                metrics = self.capacity.collector_metrics()
                errors = []
                with patch.object(self.capacity, "api_pages", side_effect=failed):
                    result = self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), errors, metrics,
                        allow_persistent_cache=True,
                    )

            self.assertEqual(result, [])
            self.assertTrue(errors)
            self.assertEqual(metrics["job_cache_hits"], 0)

    def test_in_progress_run_never_reuses_cached_job_state(self):
        with tempfile.TemporaryDirectory() as cache_root:
            run = {
                "id": 10,
                "workflow_id": 7,
                "name": "Build",
                "status": "in_progress",
                "run_attempt": 1,
                "head_sha": "abc123",
                "updated_at": "2026-09-24T12:00:00Z",
            }
            job_status = {"value": "queued"}

            def fake_api_pages(endpoint, field, errors, source, max_pages=100, *,
                               metrics=None, metric_kind=None, retries=0):
                self.capacity.record_github_call(metrics, metric_kind)
                if field == "workflow_runs":
                    return [run] if endpoint.endswith("status=in_progress") else []
                return [{
                    "id": 101,
                    "name": "test",
                    "status": job_status["value"],
                    "created_at": "2026-09-24T11:59:00Z",
                    "labels": ["self-hosted"],
                }]

            with patch.dict(os.environ, {"RUNNER_CACHE_ROOT": cache_root}):
                with patch.object(self.capacity, "api_pages", side_effect=fake_api_pages):
                    first_metrics = self.capacity.collector_metrics()
                    first = self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), [],
                        first_metrics, allow_persistent_cache=True,
                    )
                    job_status["value"] = "in_progress"
                    second_metrics = self.capacity.collector_metrics()
                    second = self.capacity.collect_queue(
                        "Example/MixedCase", datetime.now(timezone.utc), [],
                        second_metrics, allow_persistent_cache=True,
                    )

            self.assertEqual([job["job_id"] for job in first], [101])
            self.assertEqual(second, [])
            self.assertEqual(first_metrics["job_list_calls"], 1)
            self.assertEqual(second_metrics["job_list_calls"], 1)
            self.assertEqual(second_metrics["job_cache_hits"], 0)

    def test_queue_cache_ttl_is_hard_and_does_not_slide_on_hits(self):
        with tempfile.TemporaryDirectory() as cache_root:
            run = {
                "id": 10,
                "workflow_id": 7,
                "name": "Build",
                "status": "queued",
                "run_attempt": 1,
                "head_sha": "abc123",
                "updated_at": "2026-09-24T12:00:00Z",
            }

            def fake_api_pages(endpoint, field, errors, source, max_pages=100, *,
                               metrics=None, metric_kind=None, retries=0):
                self.capacity.record_github_call(metrics, metric_kind)
                if field == "workflow_runs":
                    return [run] if endpoint.endswith("status=queued") else []
                return [{
                    "id": 101,
                    "name": "test",
                    "status": "queued",
                    "created_at": "2026-09-24T11:59:00Z",
                    "labels": ["self-hosted"],
                }]

            with patch.dict(os.environ, {"RUNNER_CACHE_ROOT": cache_root}):
                with patch.object(self.capacity, "api_pages", side_effect=fake_api_pages):
                    with patch.object(self.capacity.time, "time", side_effect=[1000, 1030, 1061]):
                        first_metrics = self.capacity.collector_metrics()
                        self.capacity.collect_queue(
                            "Example/MixedCase", datetime.now(timezone.utc), [],
                            first_metrics, allow_persistent_cache=True,
                        )
                        second_metrics = self.capacity.collector_metrics()
                        self.capacity.collect_queue(
                            "Example/MixedCase", datetime.now(timezone.utc), [],
                            second_metrics, allow_persistent_cache=True,
                        )
                        third_metrics = self.capacity.collector_metrics()
                        self.capacity.collect_queue(
                            "Example/MixedCase", datetime.now(timezone.utc), [],
                            third_metrics, allow_persistent_cache=True,
                        )

            self.assertEqual(first_metrics["job_list_calls"], 1)
            self.assertEqual(second_metrics["job_list_calls"], 0)
            self.assertEqual(second_metrics["job_cache_hits"], 1)
            self.assertEqual(third_metrics["job_list_calls"], 1)
            self.assertEqual(third_metrics["job_cache_hits"], 0)

    def test_scheduler_timing_metadata_is_deterministic(self):
        metrics = self.capacity.collector_metrics()
        with patch.dict(os.environ, {"RUNNER_AUTOSCALE_INTERVAL_SECONDS": "60"}):
            self.capacity._record_scheduler_timing(metrics, 50000)
            self.assertEqual(metrics["scheduler_interval_seconds"], 60)
            self.assertEqual(metrics["scheduler_headroom_ms"], 10000)
            self.assertFalse(metrics["scheduler_overrun"])
            self.assertTrue(metrics["scheduler_near_overrun"])
            self.capacity._record_scheduler_timing(metrics, 60000)
        self.assertEqual(metrics["scheduler_headroom_ms"], 0)
        self.assertTrue(metrics["scheduler_overrun"])


if __name__ == "__main__":
    unittest.main()
