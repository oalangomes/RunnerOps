"""Local SQLite audit store. Writers are an internal API; readers never bootstrap."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from autoscale_contracts import (
    AuditError,
    action_record,
    canonical_repo,
    decision_record,
    encode,
    instant,
    label_list,
    snapshot_record,
    text,
    timestamp,
    utcnow,
)

SCHEMA_VERSION = 1
APPLICATION_ID = 0x52554E41  # RUNA
MAX_DB_BYTES = 64 * 1024 * 1024
MIGRATIONS = {
    1: (
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
        """CREATE TABLE repository_observations (
        repo_key TEXT PRIMARY KEY, repository TEXT NOT NULL, observed_at TEXT NOT NULL,
        digest TEXT NOT NULL, complete INTEGER NOT NULL CHECK (complete IN (0,1)))""",
        """CREATE TABLE queue_observations (
        observation_id TEXT PRIMARY KEY, repo_key TEXT NOT NULL REFERENCES repository_observations(repo_key) ON DELETE CASCADE,
        run_id INTEGER NOT NULL, run_attempt INTEGER NOT NULL, job_id INTEGER NOT NULL,
        first_seen_queued_at TEXT NOT NULL, last_seen_queued_at TEXT NOT NULL,
        github_created_at TEXT, required_labels TEXT NOT NULL,
        observation_count INTEGER NOT NULL CHECK (observation_count > 0),
        max_gap_seconds INTEGER NOT NULL, ended_at TEXT, end_reason TEXT,
        UNIQUE(repo_key,run_id,run_attempt,job_id,first_seen_queued_at))""",
        "CREATE INDEX queue_last_seen ON queue_observations(last_seen_queued_at)",
        "CREATE UNIQUE INDEX queue_open ON queue_observations(repo_key,run_id,run_attempt,job_id) WHERE ended_at IS NULL",
        """CREATE TABLE decisions (
        decision_id TEXT PRIMARY KEY, repository TEXT NOT NULL, timestamp TEXT NOT NULL,
        updated_at TEXT NOT NULL, payload TEXT NOT NULL CHECK (length(payload) <= 32768))""",
        "CREATE INDEX decisions_updated ON decisions(updated_at)",
        """CREATE TABLE actions (
        action_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE CASCADE,
        state TEXT NOT NULL CHECK (state IN ('planned','started','succeeded','failed','cancelled')),
        updated_at TEXT NOT NULL, payload TEXT NOT NULL CHECK (length(payload) <= 32768))""",
        "CREATE INDEX actions_decision ON actions(decision_id)",
        """CREATE TABLE action_events (
        action_id TEXT NOT NULL REFERENCES actions(action_id) ON DELETE CASCADE,
        state TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL CHECK (length(payload) <= 32768),
        PRIMARY KEY (action_id,state))""",
    )
}


class Settings:
    def __init__(
        self, retention_days=30, max_records=10000, queue_gap_seconds=300, busy_timeout_ms=2000
    ):
        for value, maximum in (
            (retention_days, 3650),
            (max_records, 100000),
            (queue_gap_seconds, 86400),
            (busy_timeout_ms, 5000),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise AuditError("invalid_settings")
        self.retention_days = retention_days
        self.max_records = max_records
        self.queue_gap_seconds = queue_gap_seconds
        self.busy_timeout_ms = busy_timeout_ms

    @classmethod
    def from_env(cls):
        try:
            return cls(
                **{
                    name: int(os.environ.get("RUNNER_AUTOSCALE_" + name.upper(), default))
                    for name, default in (
                        ("retention_days", 30),
                        ("max_records", 10000),
                        ("queue_gap_seconds", 300),
                        ("busy_timeout_ms", 2000),
                    )
                }
            )
        except ValueError:
            raise AuditError("invalid_settings") from None


def database_path():
    root = os.environ.get("RUNNER_STATE_ROOT")
    if root is None:
        root = str(
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "actions-runners"
        )
    path = Path(root) / "autoscale.db"
    if not path.is_absolute():
        raise AuditError("invalid_state_path")
    return path


def database_error(exc):
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return AuditError("store_busy")
    if "full" in message:
        return AuditError("store_full")
    if isinstance(exc, sqlite3.IntegrityError):
        return AuditError("integrity_conflict")
    return AuditError("store_invalid_or_unreadable")


class AuditStore:
    def __init__(self, path=None, *, writable=False, settings=None, clock=utcnow):
        if sqlite3.sqlite_version_info < (3, 24, 0):
            raise AuditError("sqlite_version_unsupported")
        self.path = Path(path) if path is not None else database_path()
        self.writable, self.clock = writable, clock
        self.settings = settings or Settings.from_env()
        self.connection = None
        if not self.path.is_absolute() or self.path.is_symlink():
            raise AuditError("invalid_state_path")
        # A configured state root must not turn the checkout into machine inventory.
        checkout = Path(__file__).resolve().parent
        if self.path.resolve() == checkout or checkout in self.path.resolve().parents:
            raise AuditError("state_inside_checkout")
        try:
            if writable:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if self.path.parent.stat().st_mode & 0o077:
                    raise AuditError("state_permissions_unsafe")
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(descriptor)
                except FileExistsError:
                    pass
            if not self.path.exists():
                raise AuditError("store_missing")
            if self.path.stat().st_mode & 0o077:
                raise AuditError("store_permissions_unsafe")
            mode = "rw" if writable else "ro"
            self.connection = sqlite3.connect(
                self.path.as_uri() + "?mode=" + mode,
                uri=True,
                isolation_level=None,
                timeout=self.settings.busy_timeout_ms / 1000,
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute(f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}")
            if writable:
                self._migrate()
                # Small single-host transactions: DELETE avoids WAL/SHM writes from readers.
                if self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    raise AuditError("unsupported_journal_mode")
                self.connection.execute("PRAGMA synchronous=FULL")
                page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
                if (
                    self.connection.execute(
                        f"PRAGMA max_page_count={MAX_DB_BYTES // page_size}"
                    ).fetchone()[0]
                    > MAX_DB_BYTES // page_size
                ):
                    raise AuditError("store_full")
            else:
                self.connection.execute("PRAGMA query_only=ON")
            self._check_schema()
        except (sqlite3.Error, OSError, AuditError) as exc:
            self.close()
            if isinstance(exc, AuditError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise database_error(exc) from None
            raise AuditError("store_unavailable") from None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        if not self.writable:
            raise AuditError("store_read_only")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.execute("COMMIT")
        except Exception as exc:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error):
                raise database_error(exc) from None
            raise

    def _migrate(self):
        with self.transaction():
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            app = self.connection.execute("PRAGMA application_id").fetchone()[0]
            if version > SCHEMA_VERSION or (version and app != APPLICATION_ID):
                raise AuditError("unsupported_schema")
            if version == 0 and (
                app not in (0, APPLICATION_ID)
                or self.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
                ).fetchone()
            ):
                raise AuditError("unsupported_schema")
            for next_version in range(version + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[next_version]:
                    self.connection.execute(statement)
                self.connection.execute(
                    "INSERT INTO schema_migrations VALUES (?,?)",
                    (next_version, self.clock().isoformat()),
                )
                self.connection.execute(f"PRAGMA user_version={next_version}")
            self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")

    def _check_schema(self):
        if (
            self.connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            or self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        ):
            raise AuditError("unsupported_schema")
        if [
            row[0]
            for row in self.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ] != list(range(1, SCHEMA_VERSION + 1)):
            raise AuditError("unsupported_schema")
        for table in (
            "repository_observations",
            "queue_observations",
            "decisions",
            "actions",
            "action_events",
        ):
            self.connection.execute(f"SELECT * FROM {table} LIMIT 0")
        if self.connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise AuditError("store_corrupt")
        if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise AuditError("store_corrupt")

    def _recent(self, at):
        now = self.clock()
        if not now - timedelta(days=self.settings.retention_days) <= instant(at) <= now:
            raise AuditError("timestamp_outside_retention_window")

    def observe(self, snapshot):
        record = snapshot_record(snapshot)
        at, repo = record["observed_at"], record["repo_key"]
        self._recent(at)
        digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        with self.transaction():
            previous = self.connection.execute(
                "SELECT * FROM repository_observations WHERE repo_key=?", (repo,)
            ).fetchone()
            canonical = record["repository"] or (previous["repository"] if previous else None)
            if canonical is None:
                raise AuditError("canonical_identity_unknown")
            if previous and at <= previous["observed_at"]:
                if at == previous["observed_at"] and digest == previous["digest"]:
                    return False
                raise AuditError(
                    "observation_conflict" if at == previous["observed_at"] else "stale_observation"
                )
            self._prune(protected_repo=repo)
            self.connection.execute(
                """INSERT INTO repository_observations VALUES (?,?,?,?,?)
                ON CONFLICT(repo_key) DO UPDATE SET repository=excluded.repository,observed_at=excluded.observed_at,
                digest=excluded.digest,complete=excluded.complete""",
                (repo, canonical, at, digest, record["complete"]),
            )
            open_rows = list(
                self.connection.execute(
                    "SELECT * FROM queue_observations WHERE repo_key=? AND ended_at IS NULL",
                    (repo,),
                )
            )
            by_identity = {
                (row["run_id"], row["run_attempt"], row["job_id"]): row for row in record["jobs"]
            }
            ongoing = set()
            for row in open_rows:
                identity = (row["run_id"], row["run_attempt"], row["job_id"])
                current = by_identity.get(identity)
                reason = None
                if not record["complete"]:
                    reason = "inconclusive_observation"
                elif (instant(at) - instant(row["last_seen_queued_at"])).total_seconds() > min(
                    row["max_gap_seconds"], self.settings.queue_gap_seconds
                ):
                    reason = "observation_gap"
                elif current is None:
                    reason = "left_queue"
                elif (
                    encode(current["required_labels"]) != row["required_labels"]
                    or current["github_created_at"] != row["github_created_at"]
                ):
                    reason = "evidence_changed"
                if reason:
                    self.connection.execute(
                        "UPDATE queue_observations SET ended_at=?,end_reason=? WHERE observation_id=?",
                        (at, reason, row["observation_id"]),
                    )
                else:
                    ongoing.add(identity)
                    self.connection.execute(
                        "UPDATE queue_observations SET last_seen_queued_at=?,observation_count=observation_count+1,max_gap_seconds=MIN(max_gap_seconds,?) WHERE observation_id=?",
                        (at, self.settings.queue_gap_seconds, row["observation_id"]),
                    )
            for identity, job in by_identity.items():
                if identity in ongoing:
                    continue
                observation_id = hashlib.sha256(encode([repo, *identity, at]).encode()).hexdigest()
                self.connection.execute(
                    "INSERT INTO queue_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        observation_id,
                        repo,
                        *identity,
                        at,
                        at,
                        job["github_created_at"],
                        encode(job["required_labels"]),
                        1,
                        self.settings.queue_gap_seconds,
                        None,
                        None,
                    ),
                )
            self._prune(protected_repo=repo)
        return True

    def record_decision(self, value, actions=()):
        decision = decision_record(value)
        self._recent(decision["timestamp"])
        if not isinstance(actions, (list, tuple)) or len(actions) > 100:
            raise AuditError("invalid_action_batch")
        actions = [action_record(action) for action in actions]
        if any(action["decision_id"] != decision["decision_id"] for action in actions):
            raise AuditError("invalid_action_batch")
        with self.transaction():
            previous = self.connection.execute(
                "SELECT payload FROM decisions WHERE decision_id=?", (decision["decision_id"],)
            ).fetchone()
            if previous and previous["payload"] != encode(decision):
                raise AuditError("idempotency_conflict")
            self._prune(protected_decision=decision["decision_id"])
            if not previous:
                self.connection.execute(
                    "INSERT INTO decisions VALUES (?,?,?,?,?)",
                    (
                        decision["decision_id"],
                        decision["repository"],
                        decision["timestamp"],
                        decision["timestamp"],
                        encode(decision),
                    ),
                )
            for action in actions:
                self._record_action(action)
            self._prune(protected_decision=decision["decision_id"])
        return not bool(previous)

    def record_action(self, value):
        action = action_record(value)
        with self.transaction():
            self._prune(protected_decision=action["decision_id"])
            changed = self._record_action(action)
            self._prune(protected_decision=action["decision_id"])
        return changed

    def _record_action(self, action):
        self._recent(action["timestamp"])
        payload = encode(action)
        event = self.connection.execute(
            "SELECT payload FROM action_events WHERE action_id=? AND state=?",
            (action["action_id"], action["state"]),
        ).fetchone()
        if event:
            if event["payload"] != payload:
                raise AuditError("idempotency_conflict")
            return False
        decision = self.connection.execute(
            "SELECT timestamp FROM decisions WHERE decision_id=?", (action["decision_id"],)
        ).fetchone()
        if not decision or action["timestamp"] < decision["timestamp"]:
            raise AuditError("decision_missing_or_newer")
        row = self.connection.execute(
            "SELECT payload FROM actions WHERE action_id=?", (action["action_id"],)
        ).fetchone()
        if row:
            old = json.loads(row["payload"])
            transitions = {
                "planned": ("started", "cancelled"),
                "started": ("succeeded", "failed", "cancelled"),
            }
            if (
                action["state"] not in transitions.get(old["state"], ())
                or action["timestamp"] < old["timestamp"]
                or any(action[key] != old[key] for key in ("decision_id", "kind", "target"))
                or (old["started_at"] and old["started_at"] != action["started_at"])
                or (
                    action["started_at"]
                    and not old["started_at"]
                    and action["started_at"] < old["timestamp"]
                )
                or (old["external_id"] and old["external_id"] != action["external_id"])
                or (action["finished_at"] and action["finished_at"] < old["timestamp"])
            ):
                raise AuditError("invalid_action_transition")
            self.connection.execute(
                "UPDATE actions SET state=?,updated_at=?,payload=? WHERE action_id=?",
                (action["state"], action["timestamp"], payload, action["action_id"]),
            )
        else:
            if action["state"] != "planned":
                raise AuditError("action_must_start_planned")
            if (
                self.connection.execute(
                    "SELECT COUNT(*) FROM actions WHERE decision_id=?", (action["decision_id"],)
                ).fetchone()[0]
                >= 100
            ):
                raise AuditError("action_limit")
            self.connection.execute(
                "INSERT INTO actions VALUES (?,?,?,?,?)",
                (
                    action["action_id"],
                    action["decision_id"],
                    action["state"],
                    action["timestamp"],
                    payload,
                ),
            )
        self.connection.execute(
            "INSERT INTO action_events VALUES (?,?,?,?)",
            (action["action_id"], action["state"], action["timestamp"], payload),
        )
        self.connection.execute(
            "UPDATE decisions SET updated_at=MAX(updated_at,?) WHERE decision_id=?",
            (action["timestamp"], action["decision_id"]),
        )
        return True

    def prune(self):
        with self.transaction():
            self._prune()

    def _prune(self, protected_decision=None, protected_repo=None):
        cutoff = timestamp(
            (self.clock() - timedelta(days=self.settings.retention_days)).isoformat()
        )
        terminal = "NOT EXISTS (SELECT 1 FROM actions a WHERE a.decision_id=decisions.decision_id AND a.state IN ('planned','started')) AND (? IS NULL OR decisions.decision_id != ?)"
        protected_args = (protected_decision, protected_decision)
        self.connection.execute(
            f"DELETE FROM decisions WHERE updated_at<? AND {terminal}", (cutoff, *protected_args)
        )
        self.connection.execute(
            "DELETE FROM queue_observations WHERE last_seen_queued_at<?", (cutoff,)
        )
        # Old cursors must not outlive the retention horizon.
        self.connection.execute(
            "DELETE FROM repository_observations WHERE observed_at<? AND (? IS NULL OR repo_key != ?)",
            (cutoff, protected_repo, protected_repo),
        )
        for table, identity, eligible, order in (
            ("queue_observations", "observation_id", "ended_at IS NOT NULL", "last_seen_queued_at"),
            (
                "repository_observations",
                "repo_key",
                "NOT EXISTS (SELECT 1 FROM queue_observations q WHERE q.repo_key=repository_observations.repo_key) AND (? IS NULL OR repo_key != ?)",
                "observed_at",
            ),
            ("decisions", "decision_id", terminal, "updated_at"),
        ):
            excess = (
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                - self.settings.max_records
            )
            if excess > 0:
                params = (
                    protected_args
                    if table == "decisions"
                    else (
                        (protected_repo, protected_repo)
                        if table == "repository_observations"
                        else ()
                    )
                )
                self.connection.execute(
                    f"DELETE FROM {table} WHERE {identity} IN (SELECT {identity} FROM {table} WHERE {eligible} ORDER BY {order},{identity} LIMIT ?)",
                    (*params, excess),
                )
        excess_actions = (
            self.connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            - self.settings.max_records
        )
        if excess_actions > 0:
            self.connection.execute(
                f"DELETE FROM decisions WHERE decision_id IN (SELECT decision_id FROM decisions WHERE {terminal} AND EXISTS (SELECT 1 FROM actions a WHERE a.decision_id=decisions.decision_id) ORDER BY updated_at,decision_id LIMIT ?)",
                (*protected_args, excess_actions),
            )
        for table in ("queue_observations", "repository_observations", "decisions", "actions"):
            if (
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                > self.settings.max_records
            ):
                raise AuditError("retention_capacity_exhausted")

    def _queue_rows(self, since, limit):
        rows = self.connection.execute(
            """SELECT q.*,r.repository FROM queue_observations q
            JOIN repository_observations r USING(repo_key) WHERE last_seen_queued_at>=?
            ORDER BY last_seen_queued_at DESC,observation_id LIMIT ?""",
            (since, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            del item["repo_key"]
            item["required_labels"] = label_list(json.loads(item["required_labels"]))
            item["repository"] = canonical_repo(item["repository"])
            fresh = (
                0
                <= (self.clock() - instant(item["last_seen_queued_at"])).total_seconds()
                <= item["max_gap_seconds"]
            )
            item["continuous_queued"] = item["ended_at"] is None and fresh
            item["observed_queued_seconds"] = int(
                (
                    instant(item["last_seen_queued_at"]) - instant(item["first_seen_queued_at"])
                ).total_seconds()
            )
            result.append(item)
        return result

    def history(self, since=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise AuditError("invalid_limit")
        since = timestamp(since) if since is not None else ""
        with self._read_transaction():
            rows = self.connection.execute(
                "SELECT decision_id,updated_at,payload FROM decisions WHERE updated_at>=? ORDER BY updated_at DESC,decision_id LIMIT ?",
                (since, limit + 1),
            ).fetchall()
            decisions = [
                {**decision_record(json.loads(row["payload"])), "updated_at": row["updated_at"]}
                for row in rows
            ]
            queue = self._queue_rows(since, limit + 1)
        return {
            "schema_version": 1,
            "kind": "AutoscaleHistory",
            "status": "ok",
            "since": since or None,
            "decisions": decisions[:limit],
            "queue_observations": queue[:limit],
            "limit": limit,
            "truncated": len(decisions) > limit or len(queue) > limit,
        }

    def explain(self, decision_id):
        decision_id = text(decision_id)
        with self._read_transaction():
            row = self.connection.execute(
                "SELECT payload,updated_at FROM decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            if row is None:
                raise AuditError("decision_not_found")
            actions = []
            for action in self.connection.execute(
                "SELECT action_id,payload FROM actions WHERE decision_id=? ORDER BY action_id",
                (decision_id,),
            ):
                events = [
                    action_record(json.loads(event[0]))
                    for event in self.connection.execute(
                        "SELECT payload FROM action_events WHERE action_id=? ORDER BY timestamp,rowid",
                        (action["action_id"],),
                    )
                ]
                actions.append({**action_record(json.loads(action["payload"])), "events": events})
            decision = decision_record(json.loads(row["payload"]))
        return {
            "schema_version": 1,
            "kind": "AutoscaleExplanation",
            "status": "ok",
            "decision": {**decision, "updated_at": row["updated_at"]},
            "actions": actions,
        }

    @contextmanager
    def _read_transaction(self):
        try:
            self.connection.execute("BEGIN")
            yield
        except sqlite3.Error as exc:
            raise database_error(exc) from None
        except (ValueError, TypeError, KeyError):
            raise AuditError("store_corrupt") from None
        finally:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
