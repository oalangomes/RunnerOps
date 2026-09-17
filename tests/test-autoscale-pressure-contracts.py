#!/usr/bin/env python3
"""Contracts for durable resumable aggregate pressure evidence (#107)."""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_contracts import timestamp  # noqa: E402
from autoscale_pressure import read_pressure_evidence  # noqa: E402
from autoscale_store import (  # noqa: E402
    APPLICATION_ID,
    MIGRATIONS,
    SCHEMA_VERSION,
    AuditStore,
    Settings,
)


class PressureEvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "state"
        self.root.mkdir(mode=0o700)
        self.path = self.root / "autoscale.db"
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        self.settings = Settings(queue_gap_seconds=300)

    def at(self):
        return timestamp(self.now.isoformat())

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def store(self, writable=True):
        return AuditStore(
            self.path,
            writable=writable,
            clock=lambda: self.now,
            settings=self.settings,
        )

    def job(self, job_id, labels, *, run_id=None, created_at="2026-01-01T00:00:00Z"):
        return {
            "job_id": job_id,
            "run_id": run_id or (1000 + job_id),
            "run_attempt": 1,
            "status": "queued",
            "created_at": created_at,
            "required_labels": labels,
        }

    def snapshot(self, jobs, *, complete=True):
        return {
            "schema_version": 1,
            "kind": "CapacitySnapshot",
            "observed_at": self.at(),
            "repository": {
                "nameWithOwner": "Example/Pressure",
                "match_key": "example/pressure",
            },
            "sources": {
                "repository": "complete",
                "queue": "complete" if complete else "inconclusive",
            },
            "queue": {
                "status": "complete" if complete else "inconclusive",
                "queued_job_count": len(jobs) if complete else None,
                "jobs": jobs,
            },
        }

    def pressure(self, store, *, current_only=True):
        return read_pressure_evidence(
            store.connection,
            "example/pressure",
            current_only=current_only,
        )

    def accumulate(self, store, jobs, seconds):
        store.observe(self.snapshot(jobs))
        remaining = seconds
        while remaining:
            step = min(240, remaining)
            self.advance(step)
            store.observe(self.snapshot(jobs))
            remaining -= step

    def test_qualified_pressure_suspends_and_resumes_without_counting_unknown_time(self):
        jobs = [self.job(1, ["self-hosted", "Linux", "cpu"])]
        with self.store() as store:
            self.accumulate(store, jobs, 955)
            before = self.pressure(store)[0]
            self.assertEqual(before["proven_queued_seconds"], 955)
            self.assertEqual(len(before["segments"]), 1)

            self.advance(32)
            store.observe(self.snapshot([], complete=False))
            suspended = self.pressure(store)[0]
            self.assertEqual(suspended["state"], "suspended")
            self.assertEqual(suspended["proven_queued_seconds"], 955)

            self.advance(32)
            store.observe(self.snapshot(jobs))
            resumed = self.pressure(store)[0]
            self.assertEqual(resumed["state"], "active")
            self.assertEqual(resumed["proven_queued_seconds"], 955)
            self.assertEqual(resumed["resume_count"], 1)
            self.assertEqual(resumed["last_unknown_seconds"], 64)
            self.assertEqual(len(resumed["segments"]), 2)
            self.assertEqual(resumed["segments"][1]["observed_seconds"], 0)

            self.advance(60)
            store.observe(self.snapshot(jobs))
            after = self.pressure(store)[0]
            self.assertEqual(after["proven_queued_seconds"], 1015)
            self.assertEqual(after["segments"][1]["observed_seconds"], 60)

    def test_exact_job_episode_remains_strict_while_scope_qualification_resumes(self):
        jobs = [self.job(1, ["self-hosted", "Linux", "cpu"])]
        with self.store() as store:
            self.accumulate(store, jobs, 600)
            self.advance(30)
            store.observe(self.snapshot([], complete=False))
            self.advance(30)
            store.observe(self.snapshot(jobs))

            queue = store.history()["queue_observations"]
            self.assertEqual(len(queue), 2)
            self.assertEqual(queue[1]["end_reason"], "inconclusive_observation")
            self.assertFalse(queue[1]["continuous_queued"])
            self.assertTrue(queue[0]["continuous_queued"])
            self.assertEqual(queue[0]["observation_count"], 1)

            pressure = self.pressure(store)[0]
            self.assertEqual(pressure["proven_queued_seconds"], 600)
            self.assertEqual(pressure["resume_count"], 1)

    def test_positive_disappearance_resets_qualification(self):
        jobs = [self.job(1, ["self-hosted", "Linux", "cpu"])]
        with self.store() as store:
            self.accumulate(store, jobs, 600)
            self.advance(30)
            store.observe(self.snapshot([], complete=False))
            self.advance(30)
            store.observe(self.snapshot([]))
            self.assertEqual(self.pressure(store), [])

            ended = self.pressure(store, current_only=False)[0]
            self.assertEqual(ended["state"], "ended")
            self.assertEqual(ended["end_reason"], "scope_left_queue")
            self.assertEqual(ended["proven_queued_seconds"], 600)

            self.advance(30)
            store.observe(self.snapshot(jobs))
            current = self.pressure(store)[0]
            self.assertEqual(current["proven_queued_seconds"], 0)
            self.assertEqual(current["resume_count"], 0)
            self.assertEqual(current["start_reason"], "new_scope")

    def test_excessive_unknown_gap_starts_new_qualification(self):
        jobs = [self.job(1, ["self-hosted", "Linux", "cpu"])]
        with self.store() as store:
            self.accumulate(store, jobs, 600)
            old_id = self.pressure(store)[0]["qualification_id"]
            self.advance(1)
            store.observe(self.snapshot([], complete=False))
            self.advance(300)
            store.observe(self.snapshot(jobs))

            current = self.pressure(store)[0]
            self.assertNotEqual(current["qualification_id"], old_id)
            self.assertEqual(current["proven_queued_seconds"], 0)
            self.assertEqual(current["start_reason"], "observation_gap")
            ended = [
                row
                for row in self.pressure(store, current_only=False)
                if row["qualification_id"] == old_id
            ][0]
            self.assertEqual(ended["end_reason"], "observation_gap")

    def test_label_change_resets_only_affected_scope(self):
        cpu = self.job(1, ["self-hosted", "Linux", "cpu"])
        gpu = self.job(2, ["self-hosted", "Linux", "gpu"])
        with self.store() as store:
            self.accumulate(store, [cpu, gpu], 120)
            before = {tuple(row["required_labels"]): row for row in self.pressure(store)}
            self.assertEqual(len(before), 2)

            self.advance(60)
            cpu_changed = self.job(1, ["self-hosted", "Linux", "cpu-v2"])
            store.observe(self.snapshot([cpu_changed, gpu]))
            current = {tuple(row["required_labels"]): row for row in self.pressure(store)}

            self.assertEqual(current[("gpu", "linux", "self-hosted")]["proven_queued_seconds"], 180)
            self.assertEqual(current[("cpu-v2", "linux", "self-hosted")]["proven_queued_seconds"], 0)
            ended_cpu = [
                row
                for row in self.pressure(store, current_only=False)
                if tuple(row["required_labels"]) == ("cpu", "linux", "self-hosted")
            ][0]
            self.assertEqual(ended_cpu["end_reason"], "evidence_changed")

    def test_process_restart_preserves_suspended_resume_semantics(self):
        jobs = [self.job(1, ["self-hosted", "Linux", "cpu"])]
        with self.store() as store:
            self.accumulate(store, jobs, 420)
            self.advance(20)
            store.observe(self.snapshot([], complete=False))
            self.assertEqual(self.pressure(store)[0]["state"], "suspended")

        self.advance(20)
        with self.store() as reopened:
            reopened.observe(self.snapshot(jobs))
            row = self.pressure(reopened)[0]
            self.assertEqual(row["state"], "active")
            self.assertEqual(row["proven_queued_seconds"], 420)
            self.assertEqual(row["last_unknown_seconds"], 40)
            self.assertEqual(row["resume_count"], 1)

    def test_schema_v1_is_migrated_in_place_to_v2(self):
        legacy = self.root / "legacy.db"
        connection = sqlite3.connect(legacy)
        try:
            for statement in MIGRATIONS[1]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?,?)", (1, self.at())
            )
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        finally:
            connection.close()
        os.chmod(legacy, 0o600)

        with AuditStore(
            legacy,
            writable=True,
            clock=lambda: self.now,
            settings=self.settings,
        ) as store:
            self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(
                [row[0] for row in store.connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )],
                [1, 2],
            )
            store.connection.execute("SELECT * FROM pressure_qualifications LIMIT 0")
            store.connection.execute("SELECT * FROM pressure_segments LIMIT 0")
        self.assertEqual(SCHEMA_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
