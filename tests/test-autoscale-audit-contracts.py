#!/usr/bin/env python3
"""Persistence contracts, including real SQLite transactions and public read CLI."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoscale_contracts import (  # noqa: E402 - standalone test entrypoint
    AuditError,
    timestamp,
)
from autoscale_store import (  # noqa: E402
    APPLICATION_ID,
    MIGRATIONS,
    AuditStore,
    Settings,
    database_path,
)


class AuditContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / "state" / "autoscale.db"
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
        self.env = dict(
            os.environ,
            ACTIONS_RUNNERS_HOME=str(ROOT),
            ACTIONS_RUNNERS_ENV=str(self.base / "missing.env"),
            RUNNER_STATE_ROOT=str(self.path.parent),
        )
        for key in list(self.env):
            if key.startswith("RUNNER_AUTOSCALE_"):
                del self.env[key]

    def store(self, writable=True, settings=None):
        return AuditStore(
            self.path, writable=writable, clock=lambda: self.now, settings=settings or Settings()
        )

    def at(self, seconds=0):
        return timestamp((self.now + timedelta(seconds=seconds)).isoformat())

    def advance(self, seconds=30):
        self.now += timedelta(seconds=seconds)

    def snapshot(self, jobs=(123,), *, attempt=1, complete=True):
        return {
            "schema_version": 1,
            "kind": "CapacitySnapshot",
            "observed_at": self.at(),
            "repository": {"nameWithOwner": "Example/MixedCase", "match_key": "example/mixedcase"},
            "sources": {
                "repository": "complete",
                "queue": "complete" if complete else "inconclusive",
            },
            "queue": {
                "status": "complete" if complete else "inconclusive",
                "queued_job_count": len(jobs) if complete else None,
                "jobs": [
                    {
                        "job_id": job,
                        "run_id": 7,
                        "run_attempt": attempt,
                        "status": "queued",
                        "created_at": "2020-01-01T00:00:00Z",
                        "required_labels": ["self-hosted", "Linux"],
                    }
                    for job in jobs
                ],
            },
        }

    def decision(self, identity="decision-1"):
        return {
            "decision_id": identity,
            "timestamp": self.at(),
            "repository": "Example/MixedCase",
            "policy_fingerprint": "sha256:" + "a" * 64,
            "decision": "WAIT",
            "reason_codes": ["CAPACITY_BUSY"],
            "requested_capacity_delta": 0,
            "evidence": {
                "observed_at": self.at(),
                "queue_status": "complete",
                "queued_job_count": 1,
                "queue": [
                    {
                        "job_id": 123,
                        "run_id": 7,
                        "run_attempt": 1,
                        "first_seen_queued_at": self.at(-30),
                        "last_seen_queued_at": self.at(),
                        "continuous_queued": True,
                        "required_labels": ["self-hosted", "Linux"],
                    }
                ],
                "capacity": {
                    "available_now": 0,
                    "busy_capacity": 1,
                    "provisioned_idle": 0,
                    "inconclusive": 0,
                    "active_local_runner_count": 1,
                },
                "active_burst_capacity": None,
            },
        }

    def action(self, identity="action-1", decision="decision-1"):
        return {
            "action_id": identity,
            "decision_id": decision,
            "kind": "START_LOCAL",
            "target": "runner-example",
            "state": "planned",
            "timestamp": self.at(),
            "started_at": None,
            "finished_at": None,
            "external_id": None,
            "diagnostic": {"code": None, "exit_code": None},
        }

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(AuditError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def files(self):
        return {
            str(path): path.read_bytes() for path in self.path.parent.rglob("*") if path.is_file()
        }

    def cli(self, *args, code=0, env=None, json_output=True):
        before = self.files()
        result = subprocess.run(
            [str(ROOT / "runnerctl"), "autoscale", *args, *(["--json"] if json_output else [])],
            env=env or self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, code, result.stderr + result.stdout)
        self.assertEqual(
            before, self.files(), "read commands must not create or mutate DB/sidecars"
        )
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout) if json_output else result.stdout + result.stderr

    def test_runnerctl_init_prepares_private_state_for_audit_writer(self):
        xdg = self.base / "xdg"
        env = dict(
            os.environ,
            HOME=str(self.base / "home"),
            XDG_CONFIG_HOME=str(xdg / "config"),
            XDG_DATA_HOME=str(xdg / "data"),
            XDG_CACHE_HOME=str(xdg / "cache"),
            XDG_STATE_HOME=str(xdg / "state"),
            ACTIONS_RUNNERS_ENV=str(self.base / "missing.env"),
        )
        Path(env["HOME"]).mkdir()
        # Reproduce the normal permissive shell default that exposed the integration bug.
        result = subprocess.run(
            ["bash", "-c", 'umask 022; exec "$1" init', "runnerops-init", str(ROOT / "runnerctl")],
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        state_root = xdg / "state" / "actions-runners"
        self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)

        # The real init path must produce state accepted by the real audit writer.
        with patch.dict(os.environ, {"RUNNER_STATE_ROOT": str(state_root)}):
            with AuditStore(writable=True, clock=lambda: self.now, settings=Settings()) as store:
                self.assertEqual(store.path, state_root / "autoscale.db")
                self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

        # Upgrade path: init tightens an already-existing permissive state root.
        state_root.chmod(0o755)
        result = subprocess.run(
            ["bash", "-c", 'umask 022; exec "$1" init', "runnerops-init", str(ROOT / "runnerctl")],
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)

    def test_bootstrap_schema_permissions_and_default_path(self):
        with self.store() as store:
            connection = store.connection
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID
            )
            self.assertEqual(
                connection.execute("SELECT version FROM schema_migrations").fetchone()[0], 1
            )
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 2000)
            self.assertLessEqual(
                connection.execute("PRAGMA max_page_count").fetchone()[0] * 4096, 64 * 1024 * 1024
            )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        with patch.dict(os.environ, {"RUNNER_STATE_ROOT": str(self.base / "custom")}):
            self.assertEqual(database_path(), self.base / "custom" / "autoscale.db")
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base)}, clear=True):
            self.assertEqual(database_path(), self.base / "actions-runners" / "autoscale.db")

    def test_first_last_seen_reopen_and_replay(self):
        first = self.snapshot()
        with self.store() as store:
            self.assertTrue(store.observe(first))
            self.assertFalse(store.observe(first))
        self.advance()
        second = self.snapshot()
        with self.store() as store:
            store.observe(second)
        with self.store(False) as store:
            rows = store.history()["queue_observations"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["first_seen_queued_at"], first["observed_at"])
            self.assertEqual(rows[0]["last_seen_queued_at"], second["observed_at"])
            self.assertEqual(rows[0]["observation_count"], 2)
            self.assertEqual(rows[0]["observed_queued_seconds"], 30)
            self.assertTrue(rows[0]["continuous_queued"])
            self.assertEqual(rows[0]["repository"], "Example/MixedCase")
            self.assertEqual(rows[0]["github_created_at"], timestamp("2020-01-01T00:00:00Z"))
            self.error("store_read_only", store.observe, second)
        # Durability across a different interpreter, not just connection objects.
        observed = self.cli("history", "--since", "24h")["queue_observations"][0]
        self.assertEqual(observed["first_seen_queued_at"], first["observed_at"])

    def test_disappearing_and_reappearing_job_starts_new_episode(self):
        with self.store() as store:
            store.observe(self.snapshot())
            self.advance()
            store.observe(self.snapshot(jobs=()))
            self.assertFalse(store.history()["queue_observations"][0]["continuous_queued"])
            self.advance()
            store.observe(self.snapshot())
            rows = store.history()["queue_observations"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["observation_count"], 1)
            self.assertEqual(rows[1]["end_reason"], "left_queue")
            self.assertTrue(rows[0]["continuous_queued"])

    def test_rerun_does_not_mix_attempts(self):
        with self.store() as store:
            store.observe(self.snapshot())
            self.advance()
            store.observe(self.snapshot(attempt=2))
            rows = store.history()["queue_observations"]
            self.assertEqual({row["run_attempt"] for row in rows}, {1, 2})
            self.assertEqual(sum(row["continuous_queued"] for row in rows), 1)

    def test_partial_collection_or_identity_failure_breaks_continuity(self):
        for identity_failure in (False, True):
            with self.subTest(identity_failure=identity_failure):
                with self.store() as store:
                    store.observe(self.snapshot())
                    self.advance()
                    partial = self.snapshot(complete=False)
                    if identity_failure:
                        partial["repository"]["nameWithOwner"] = None
                        partial["sources"]["repository"] = "inconclusive"
                    store.observe(partial)
                    rows = store.history()["queue_observations"]
                    self.assertTrue(all(not row["continuous_queued"] for row in rows))
                    self.assertTrue(all(row["repository"] == "Example/MixedCase" for row in rows))
                    self.assertIn("inconclusive_observation", [row["end_reason"] for row in rows])
                self.advance()

    def test_gap_staleness_and_clock_regression(self):
        with self.store() as store:
            original = self.snapshot()
            store.observe(original)
            self.advance(301)
            self.assertFalse(store.history()["queue_observations"][0]["continuous_queued"])
            store.observe(self.snapshot())
            rows = store.history()["queue_observations"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["end_reason"], "observation_gap")
            self.error("stale_observation", store.observe, original)
            changed = self.snapshot(jobs=())
            self.error("observation_conflict", store.observe, changed)
            future = self.snapshot()
            future["observed_at"] = self.at(1)
            self.error("timestamp_outside_retention_window", store.observe, future)

    def test_label_change_preserves_previous_evidence(self):
        with self.store() as store:
            store.observe(self.snapshot())
            self.advance()
            changed = self.snapshot()
            changed["queue"]["jobs"][0]["required_labels"] = ["GPU"]
            store.observe(changed)
            rows = store.history()["queue_observations"]
            self.assertEqual(rows[0]["required_labels"], ["GPU"])
            self.assertEqual(rows[1]["end_reason"], "evidence_changed")

    def test_decision_and_action_durability_idempotency_and_outcomes(self):
        decision, action = self.decision(), self.action()
        with self.store() as store:
            self.assertTrue(store.record_decision(decision, [action]))
            self.assertFalse(store.record_decision(decision, [action]))
            self.assertFalse(store.record_action(action))
            self.advance()
            started = {
                **action,
                "state": "started",
                "timestamp": self.at(),
                "started_at": self.at(),
            }
            store.record_action(started)
            self.advance()
            finished = {
                **started,
                "state": "failed",
                "timestamp": self.at(),
                "finished_at": self.at(),
                "diagnostic": {"code": "SERVICE_START_FAILED", "exit_code": 1},
            }
            store.record_action(finished)
            self.assertFalse(store.record_action(action))
            self.assertFalse(store.record_action(finished))
        with self.store(False) as store:
            explanation = store.explain("decision-1")
            self.assertEqual(explanation["decision"]["repository"], "Example/MixedCase")
            self.assertEqual(len(explanation["actions"]), 1)
            self.assertEqual(explanation["actions"][0]["state"], "failed")
            self.assertEqual(
                [event["state"] for event in explanation["actions"][0]["events"]],
                ["planned", "started", "failed"],
            )
            self.assertEqual(explanation["decision"]["updated_at"], self.at())

    def test_conflicting_replay_and_invalid_transition(self):
        decision, action = self.decision(), self.action()
        with self.store() as store:
            store.record_decision(decision, [action])
            self.error(
                "idempotency_conflict", store.record_decision, {**decision, "decision": "HOLD"}
            )
            self.error(
                "idempotency_conflict", store.record_action, {**action, "target": "different"}
            )
            self.error(
                "invalid_action_transition",
                store.record_action,
                {**action, "state": "succeeded", "started_at": self.at(), "finished_at": self.at()},
            )

    def test_transaction_failure_rolls_back_decision_and_actions(self):
        with self.store() as store:
            conflicting = {**self.action(), "target": "other"}
            self.error(
                "idempotency_conflict",
                store.record_decision,
                self.decision(),
                [self.action(), conflicting],
            )
            self.assertEqual(store.history()["decisions"], [])
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 0
            )
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM action_events").fetchone()[0], 0
            )
            # SQLite itself fails mid-observation; earlier inserts must roll back.
            store.connection.execute(
                "CREATE TRIGGER fail_queue BEFORE INSERT ON queue_observations WHEN NEW.job_id=999 BEGIN SELECT RAISE(ABORT, 'fixture'); END"
            )
            self.error("integrity_conflict", store.observe, self.snapshot(jobs=(123, 999)))
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM repository_observations").fetchone()[
                    0
                ],
                0,
            )
            self.assertEqual(store.history()["queue_observations"], [])

    def test_action_outcome_cannot_predate_planning(self):
        with self.store() as store:
            action = self.action()
            store.record_decision(self.decision(), [action])
            self.error(
                "invalid_action_transition",
                store.record_action,
                {**action, "state": "cancelled", "finished_at": self.at(-1)},
            )
            self.assertEqual(store.explain("decision-1")["actions"][0]["state"], "planned")

    def test_migration_failure_is_atomic_and_newer_schema_is_rejected(self):
        with patch.dict(MIGRATIONS, {1: (*MIGRATIONS[1], "INVALID SQL")}):
            self.error("store_invalid_or_unreadable", self.store)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0],
                0,
            )
        with self.store() as store:
            store.connection.execute("PRAGMA user_version=99")
        before = self.path.read_bytes()
        self.error("unsupported_schema", self.store)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.cli("history", code=3)["error"], "unsupported_schema")

    def test_retention_keeps_active_actions_and_retains_recent_outcome(self):
        with self.store() as store:
            store.record_decision(self.decision("old"))
            store.record_decision(self.decision(), [self.action()])
            store.observe(self.snapshot())
            self.advance(31 * 86400)
            store.prune()
            self.assertEqual(
                [row["decision_id"] for row in store.history()["decisions"]], ["decision-1"]
            )
            self.assertEqual(store.history()["queue_observations"], [])
            cancelled = {**self.action(), "state": "cancelled", "finished_at": self.at()}
            store.record_action(cancelled)
            self.assertEqual(len(store.history()["decisions"]), 1)
            self.advance(31 * 86400)
            store.prune()
            self.assertEqual(store.history()["decisions"], [])
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM action_events").fetchone()[0], 0
            )

    def test_row_caps_prune_closed_records_and_fail_safe_for_active(self):
        with self.store(settings=Settings(max_records=2)) as store:
            for number in range(4):
                store.record_decision(self.decision(f"decision-{number}"))
                store.observe(self.snapshot(jobs=(number + 1,)))
                self.advance()
            self.assertEqual(len(store.history()["decisions"]), 2)
            self.assertEqual(len(store.history()["queue_observations"]), 2)
        with self.store(settings=Settings(max_records=2)) as store:
            before = store.history()
            self.error(
                "retention_capacity_exhausted", store.observe, self.snapshot(jobs=(10, 11, 12))
            )
            self.assertEqual(store.history(), before)

    def test_busy_timeout_is_bounded(self):
        with self.store() as writer:
            with self.store(settings=Settings(busy_timeout_ms=50)) as contender:
                writer.connection.execute("BEGIN IMMEDIATE")
                begin = time.monotonic()
                self.error("store_busy", contender.record_decision, self.decision())
                self.assertLess(time.monotonic() - begin, 1)
                self.assertFalse(contender.connection.in_transaction)
                writer.connection.execute("ROLLBACK")
                self.assertTrue(contender.record_decision(self.decision()))

    def test_full_store_does_not_silently_evict_incoming_decision(self):
        with self.store(settings=Settings(max_records=1)) as store:
            store.record_decision(self.decision(), [self.action()])
            self.advance()
            self.error(
                "retention_capacity_exhausted", store.record_decision, self.decision("incoming")
            )
            self.assertEqual(
                [row["decision_id"] for row in store.history()["decisions"]], ["decision-1"]
            )
            self.assertEqual(len(store.explain("decision-1")["actions"]), 1)

    def test_malformed_stored_payload_has_explicit_error(self):
        with self.store() as store:
            store.record_decision(self.decision())
            store.connection.execute("UPDATE decisions SET payload='not-json'")
        self.assertEqual(self.cli("history", code=3)["error"], "store_corrupt")
        self.assertEqual(
            self.cli("explain", "--decision", "decision-1", code=3)["error"], "store_corrupt"
        )

    def test_history_explain_json_and_human_outputs(self):
        with self.store() as store:
            store.record_decision(self.decision(), [self.action()])
            store.observe(self.snapshot())
        history = self.cli("history", "--since", "24h")
        self.assertEqual(
            (history["schema_version"], history["kind"], history["status"]),
            (1, "AutoscaleHistory", "ok"),
        )
        self.assertEqual(len(history["decisions"]), 1)
        self.assertEqual(history["queue_observations"][0]["repository"], "Example/MixedCase")
        result = self.cli("explain", "--decision", "decision-1")
        self.assertEqual(result["kind"], "AutoscaleExplanation")
        self.assertEqual(result["actions"][0]["decision_id"], result["decision"]["decision_id"])
        self.assertIn("CAPACITY_BUSY", self.cli("history", json_output=False))
        self.assertIn(
            "Action action-1", self.cli("explain", "--decision", "decision-1", json_output=False)
        )
        self.assertEqual(
            self.cli("explain", "--decision", "absent", code=3)["error"], "decision_not_found"
        )
        self.assertEqual(self.cli("history", "--since", "1s")["decisions"], [])
        self.cli("history", "--since", "bogus", code=2, json_output=False)

    def test_missing_and_corrupt_database_are_explicit_without_bootstrap(self):
        self.assertEqual(self.cli("history", code=3)["error"], "store_missing")
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir(mode=0o700)
        self.path.write_bytes(b"not a sqlite database")
        self.path.chmod(0o600)
        self.assertEqual(self.cli("history", code=3)["error"], "store_invalid_or_unreadable")
        self.error("store_invalid_or_unreadable", self.store)
        self.assertEqual(self.path.read_bytes(), b"not a sqlite database")

    def test_secret_rejection_projection_and_no_raw_payload_on_disk(self):
        secrets = [
            "ghp_TestCredential123456",
            "github_pat_Private123456",
            "AKIAABCDEFGHIJKLMNOP",
            "opaque-registration-token-value",
        ]
        with patch.dict(os.environ, {"GH_TOKEN": secrets[-1]}):
            with self.store() as store:
                snapshot = self.snapshot()
                snapshot["environment"] = {"GH_TOKEN": secrets[-1]}
                snapshot["queue"]["jobs"][0]["workflow_name"] = secrets[0]
                snapshot["queue"]["jobs"][0]["name"] = secrets[1]
                store.observe(snapshot)
                self.advance()
                for secret in secrets:
                    contaminated = self.decision()
                    contaminated["decision_id"] = secret
                    self.error("secret_rejected", store.record_decision, contaminated)
                    contaminated_snapshot = self.snapshot()
                    contaminated_snapshot["queue"]["jobs"][0]["required_labels"] = [secret]
                    self.error("secret_rejected", store.observe, contaminated_snapshot)
                for key in (
                    "GH_TOKEN",
                    "registration_token",
                    "cloud_credentials",
                    "env",
                    "workflow",
                ):
                    contaminated = self.decision()
                    contaminated["evidence"][key] = secrets[-1]
                    self.error("invalid_fields", store.record_decision, contaminated)
                store.record_decision(self.decision(), [self.action()])
                action = {**self.action("unsafe-action"), "diagnostic": {"message": secrets[0]}}
                self.error("invalid_fields", store.record_action, action)
        for data in self.files().values():
            for secret in secrets:
                self.assertNotIn(secret.encode(), data)

    def test_sql_injection_and_payload_bounds_rejected(self):
        with self.store() as store:
            decision = self.decision()
            decision["decision_id"] = "x'); DROP TABLE decisions;--"
            self.error("invalid_identifier", store.record_decision, decision)
            decision = self.decision()
            decision["evidence"]["queue"] *= 101
            self.error("payload_too_large", store.record_decision, decision)
            self.assertEqual(store.history()["decisions"], [])

    def test_non_audit_commands_work_without_sqlite(self):
        pythonpath = self.base / "python"
        pythonpath.mkdir()
        (pythonpath / "sitecustomize.py").write_text("""import sys
class NoSQLite:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'sqlite3':
            raise ImportError('sqlite unavailable in fixture')
sys.meta_path.insert(0, NoSQLite())
""")
        env = dict(self.env, PYTHONPATH=str(pythonpath))
        self.assertEqual(
            self.cli("history", code=3, env=env)["error"], "sqlite_capability_unavailable"
        )
        for args in [("capacity", "--help"), ("autoscale", "status", "--help")]:
            result = subprocess.run([str(ROOT / "runnerctl"), *args], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        platform = self.base / "platform"
        platform.mkdir()
        (platform / "runner-runtime-env.sh").write_bytes(
            (ROOT / "runner-runtime-env.sh").read_bytes()
        )
        for name in ("runners.sh", "runner-services.sh", "ci-watch.sh"):
            script = platform / name
            script.write_text('#!/bin/sh\nprintf "legacy-ok\\n"\n')
            script.chmod(0o755)
        env["ACTIONS_RUNNERS_HOME"] = str(platform)
        for args in [
            ("status", "example"),
            ("start", "example"),
            ("health", "example"),
            ("doctor", "example"),
            ("ci", "watch", "example/project", "--sha", "1234567"),
        ]:
            result = subprocess.run(
                [str(ROOT / "runnerctl"), *args], env=env, capture_output=True, text=True
            )
            self.assertEqual(
                (result.returncode, result.stdout.strip()), (0, "legacy-ok"), result.stderr
            )

    def test_history_is_bounded_and_settings_are_validated(self):
        with self.store() as store:
            for number in range(3):
                store.record_decision(self.decision(f"decision-{number}"))
            result = store.history(limit=2)
            self.assertEqual(len(result["decisions"]), 2)
            self.assertTrue(result["truncated"])
        self.error("invalid_settings", Settings, retention_days=0)
        self.error("invalid_settings", Settings, busy_timeout_ms=5001)
        env = dict(self.env, RUNNER_AUTOSCALE_RETENTION_DAYS="not-a-number")
        self.assertEqual(self.cli("history", code=3, env=env)["error"], "invalid_settings")

    def test_unsafe_permissions_and_checkout_storage_are_rejected(self):
        with self.store():
            pass
        self.path.chmod(0o644)
        self.assertEqual(self.cli("history", code=3)["error"], "store_permissions_unsafe")
        self.error("state_inside_checkout", AuditStore, ROOT / "autoscale.db", writable=True)
        self.assertFalse((ROOT / "autoscale.db").exists())


if __name__ == "__main__":
    unittest.main()
