"""Durable aggregate queue-pressure evidence for autoscaling.

Exact per-job queue episodes remain owned by ``autoscale_store``. This module
tracks a separate capability-scope qualification chain made only of explicitly
observed segments. Inconclusive observations suspend a chain; a bounded resume
starts a new observed segment without adding the unknown interval to proven time.
"""

import hashlib
import json

from autoscale_contracts import AuditError, encode, instant, label_list, timestamp

PRESSURE_TABLES = ("pressure_qualifications", "pressure_segments")
PRESSURE_MIGRATION = (
    """CREATE TABLE pressure_qualifications (
    qualification_id TEXT PRIMARY KEY,
    repo_key TEXT NOT NULL REFERENCES repository_observations(repo_key) ON DELETE CASCADE,
    scope_key TEXT NOT NULL,
    required_labels TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active','suspended','ended')),
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    max_gap_seconds INTEGER NOT NULL CHECK (max_gap_seconds > 0),
    suspended_at TEXT,
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count >= 0),
    last_resume_at TEXT,
    last_unknown_seconds INTEGER CHECK (last_unknown_seconds IS NULL OR last_unknown_seconds >= 0),
    start_reason TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    UNIQUE(repo_key,scope_key,first_observed_at))""",
    """CREATE UNIQUE INDEX pressure_current
    ON pressure_qualifications(repo_key,scope_key)
    WHERE state IN ('active','suspended')""",
    "CREATE INDEX pressure_last_observed ON pressure_qualifications(last_observed_at)",
    """CREATE TABLE pressure_segments (
    segment_id TEXT PRIMARY KEY,
    qualification_id TEXT NOT NULL REFERENCES pressure_qualifications(qualification_id) ON DELETE CASCADE,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL CHECK (observation_count > 0),
    UNIQUE(qualification_id,first_observed_at))""",
    "CREATE INDEX pressure_segments_qualification ON pressure_segments(qualification_id,first_observed_at)",
)


def _normalized_scope(labels):
    """Return RunnerOps' case-insensitive exact capability scope."""
    normalized = label_list(labels)
    if not normalized:
        return None
    return tuple(sorted({label.casefold() for label in normalized}))


def _scope_key(scope):
    return hashlib.sha256(encode(list(scope)).encode("utf-8")).hexdigest()


def _qualification_id(repo_key, scope_key, at):
    return "pressure-" + hashlib.sha256(
        encode([repo_key, scope_key, timestamp(at)]).encode("utf-8")
    ).hexdigest()[:40]


def _segment_id(qualification_id, at):
    return "segment-" + hashlib.sha256(
        encode([qualification_id, timestamp(at)]).encode("utf-8")
    ).hexdigest()[:40]


def check_schema(connection):
    for table in PRESSURE_TABLES:
        connection.execute(f"SELECT * FROM {table} LIMIT 0")


def _current_scopes(record):
    result = {}
    for job in record["jobs"] if record["complete"] else []:
        scope = _normalized_scope(job.get("required_labels"))
        if scope is None:
            continue
        key = _scope_key(scope)
        result[key] = list(scope)
    return result


def _evidence_changed_scopes(connection, repo_key, at):
    scopes = set()
    rows = connection.execute(
        """SELECT required_labels FROM queue_observations
        WHERE repo_key=? AND ended_at=? AND end_reason='evidence_changed'""",
        (repo_key, at),
    ).fetchall()
    for row in rows:
        try:
            scope = _normalized_scope(json.loads(row["required_labels"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise AuditError("store_corrupt") from None
        if scope is not None:
            scopes.add(_scope_key(scope))
    return scopes


def _start(connection, repo_key, scope_key, labels, at, max_gap_seconds, reason):
    qualification_id = _qualification_id(repo_key, scope_key, at)
    connection.execute(
        """INSERT INTO pressure_qualifications (
        qualification_id,repo_key,scope_key,required_labels,state,
        first_observed_at,last_observed_at,max_gap_seconds,suspended_at,
        resume_count,last_resume_at,last_unknown_seconds,start_reason,ended_at,end_reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            qualification_id,
            repo_key,
            scope_key,
            encode(labels),
            "active",
            at,
            at,
            max_gap_seconds,
            None,
            0,
            None,
            None,
            reason,
            None,
            None,
        ),
    )
    connection.execute(
        "INSERT INTO pressure_segments VALUES (?,?,?,?,?)",
        (_segment_id(qualification_id, at), qualification_id, at, at, 1),
    )
    return qualification_id


def _end(connection, qualification_id, at, reason):
    connection.execute(
        """UPDATE pressure_qualifications
        SET state='ended',ended_at=?,end_reason=?,suspended_at=NULL
        WHERE qualification_id=?""",
        (at, reason, qualification_id),
    )


def _continue_active(connection, row, at, queue_gap_seconds):
    qualification_id = row["qualification_id"]
    segment = connection.execute(
        """SELECT segment_id FROM pressure_segments
        WHERE qualification_id=? ORDER BY first_observed_at DESC LIMIT 1""",
        (qualification_id,),
    ).fetchone()
    if segment is None:
        raise AuditError("store_corrupt")
    connection.execute(
        """UPDATE pressure_segments
        SET last_observed_at=?,observation_count=observation_count+1
        WHERE segment_id=?""",
        (at, segment["segment_id"]),
    )
    connection.execute(
        """UPDATE pressure_qualifications
        SET last_observed_at=?,max_gap_seconds=MIN(max_gap_seconds,?)
        WHERE qualification_id=?""",
        (at, queue_gap_seconds, qualification_id),
    )


def _resume(connection, row, at, queue_gap_seconds, unknown_seconds):
    qualification_id = row["qualification_id"]
    connection.execute(
        """UPDATE pressure_qualifications
        SET state='active',last_observed_at=?,max_gap_seconds=MIN(max_gap_seconds,?),
            suspended_at=NULL,resume_count=resume_count+1,last_resume_at=?,
            last_unknown_seconds=?
        WHERE qualification_id=?""",
        (at, queue_gap_seconds, at, unknown_seconds, qualification_id),
    )
    connection.execute(
        "INSERT INTO pressure_segments VALUES (?,?,?,?,?)",
        (_segment_id(qualification_id, at), qualification_id, at, at, 1),
    )


def observe_pressure(connection, record, settings):
    """Advance aggregate pressure state inside the caller's write transaction."""
    repo_key = record["repo_key"]
    at = timestamp(record["observed_at"])
    current = connection.execute(
        """SELECT * FROM pressure_qualifications
        WHERE repo_key=? AND state IN ('active','suspended')
        ORDER BY scope_key""",
        (repo_key,),
    ).fetchall()

    if not record["complete"]:
        for row in current:
            if row["state"] == "active":
                connection.execute(
                    """UPDATE pressure_qualifications
                    SET state='suspended',suspended_at=?
                    WHERE qualification_id=?""",
                    (at, row["qualification_id"]),
                )
        return

    scopes = _current_scopes(record)
    changed_scopes = _evidence_changed_scopes(connection, repo_key, at)
    handled = set()

    for row in current:
        scope_key = row["scope_key"]
        labels = scopes.get(scope_key)
        handled.add(scope_key)

        if scope_key in changed_scopes:
            _end(connection, row["qualification_id"], at, "evidence_changed")
            if labels is not None:
                _start(
                    connection,
                    repo_key,
                    scope_key,
                    labels,
                    at,
                    settings.queue_gap_seconds,
                    "evidence_changed",
                )
            continue

        if labels is None:
            _end(connection, row["qualification_id"], at, "scope_left_queue")
            continue

        gap_seconds = int(
            (instant(at) - instant(row["last_observed_at"])).total_seconds()
        )
        if gap_seconds < 0:
            raise AuditError("store_corrupt")
        allowed_gap = min(row["max_gap_seconds"], settings.queue_gap_seconds)
        if gap_seconds > allowed_gap:
            _end(connection, row["qualification_id"], at, "observation_gap")
            _start(
                connection,
                repo_key,
                scope_key,
                labels,
                at,
                settings.queue_gap_seconds,
                "observation_gap",
            )
            continue

        if row["state"] == "suspended":
            _resume(connection, row, at, settings.queue_gap_seconds, gap_seconds)
        elif row["state"] == "active":
            _continue_active(connection, row, at, settings.queue_gap_seconds)
        else:
            raise AuditError("store_corrupt")

    for scope_key, labels in sorted(scopes.items()):
        if scope_key not in handled:
            _start(
                connection,
                repo_key,
                scope_key,
                labels,
                at,
                settings.queue_gap_seconds,
                "new_scope",
            )


def _segments(connection, qualification_id):
    rows = connection.execute(
        """SELECT * FROM pressure_segments WHERE qualification_id=?
        ORDER BY first_observed_at,segment_id""",
        (qualification_id,),
    ).fetchall()
    result = []
    proven = 0
    for row in rows:
        first = timestamp(row["first_observed_at"])
        last = timestamp(row["last_observed_at"])
        seconds = int((instant(last) - instant(first)).total_seconds())
        if seconds < 0 or row["observation_count"] <= 0:
            raise AuditError("store_corrupt")
        proven += seconds
        result.append(
            {
                "first_observed_at": first,
                "last_observed_at": last,
                "observed_seconds": seconds,
                "observation_count": row["observation_count"],
            }
        )
    if not result:
        raise AuditError("store_corrupt")
    return result, proven


def read_pressure_evidence(connection, repo_key, *, current_only=True, limit=1000):
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise AuditError("invalid_limit")
    where = "repo_key=?"
    params = [repo_key]
    if current_only:
        where += " AND state IN ('active','suspended')"
    rows = connection.execute(
        f"""SELECT * FROM pressure_qualifications WHERE {where}
        ORDER BY last_observed_at DESC,qualification_id LIMIT ?""",
        (*params, limit + 1),
    ).fetchall()
    if len(rows) > limit:
        raise AuditError("planner_evidence_too_large")
    result = []
    for row in rows:
        segments, proven = _segments(connection, row["qualification_id"])
        try:
            labels = label_list(json.loads(row["required_labels"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise AuditError("store_corrupt") from None
        result.append(
            {
                "qualification_id": row["qualification_id"],
                "required_labels": labels,
                "state": row["state"],
                "first_observed_at": timestamp(row["first_observed_at"]),
                "last_observed_at": timestamp(row["last_observed_at"]),
                "proven_queued_seconds": proven,
                "max_gap_seconds": row["max_gap_seconds"],
                "suspended_at": (
                    timestamp(row["suspended_at"])
                    if row["suspended_at"] is not None
                    else None
                ),
                "resume_count": row["resume_count"],
                "last_resume_at": (
                    timestamp(row["last_resume_at"])
                    if row["last_resume_at"] is not None
                    else None
                ),
                "last_unknown_seconds": row["last_unknown_seconds"],
                "start_reason": row["start_reason"],
                "ended_at": timestamp(row["ended_at"]) if row["ended_at"] else None,
                "end_reason": row["end_reason"],
                "segments": segments,
            }
        )
    return result


def prune_pressure(connection, cutoff, max_records):
    """Apply the same bounded-retention intent as the audit store."""
    connection.execute(
        "DELETE FROM pressure_qualifications WHERE last_observed_at<?",
        (cutoff,),
    )
    excess = connection.execute(
        "SELECT COUNT(*) FROM pressure_qualifications"
    ).fetchone()[0] - max_records
    if excess > 0:
        connection.execute(
            """DELETE FROM pressure_qualifications WHERE qualification_id IN (
            SELECT qualification_id FROM pressure_qualifications
            WHERE state='ended'
            ORDER BY COALESCE(ended_at,last_observed_at),qualification_id LIMIT ?)
            """,
            (excess,),
        )
    for table in PRESSURE_TABLES:
        if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > max_records:
            raise AuditError("retention_capacity_exhausted")
