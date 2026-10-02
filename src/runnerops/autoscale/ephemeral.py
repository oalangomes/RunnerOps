"""Bounded local ephemeral policy and read-only lifecycle capacity evidence."""

import hashlib
import itertools
import os
from pathlib import Path

from runnerops.ephemeral.contracts import LifecycleState
from runnerops.ephemeral.identity import runner_identity, validate_action_id
from runnerops.ephemeral.store import ActionStore

from .contracts import AuditError, canonical_repo, label_list
from .provision import PROFILES


MAX_EVIDENCE_ACTIONS = 1000


def load_ephemeral_policy():
    def integer(name, default, minimum, maximum):
        try:
            value = int(os.environ.get(name, str(default)).strip())
        except ValueError:
            raise ValueError("invalid_local_ephemeral_policy") from None
        if not minimum <= value <= maximum:
            raise ValueError("invalid_local_ephemeral_policy")
        return value

    raw = os.environ.get("RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_ENABLED", "false").strip().lower()
    if raw not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        raise ValueError("invalid_local_ephemeral_policy")
    enabled = raw in ("true", "1", "yes", "on")
    maximum = integer("RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_EPHEMERALS", 1, 1, 1000)
    cooldown = integer("RUNNER_AUTOSCALE_EPHEMERAL_SCALE_OUT_COOLDOWN_SECONDS", 30, 1, 86400 * 30)
    profile = os.environ.get("RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_PROFILE", "").strip().lower()
    raw_labels = os.environ.get("RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_LABELS", "").strip()
    try:
        labels = label_list([part.strip() for part in raw_labels.split(",")]) if raw_labels else []
    except AuditError:
        raise ValueError("invalid_local_ephemeral_policy") from None
    if profile and profile not in PROFILES:
        raise ValueError("invalid_local_ephemeral_policy")
    if enabled and (not profile or not labels or len(labels) > 31
                    or "self-hosted" not in {x.casefold() for x in labels}):
        raise ValueError("invalid_local_ephemeral_policy")
    return {"enabled": enabled, "max_active": maximum, "cooldown_seconds": cooldown,
            "profile": profile or None, "labels": labels}


def exact_ephemeral_id(decision_id):
    return hashlib.sha256((decision_id + "\0CREATE_EPHEMERAL").encode("utf-8")).hexdigest()[:32]


def _state_root():
    return Path(os.environ.get("RUNNER_STATE_ROOT", str(Path.home() / ".local/state/actions-runners")))


def read_ephemeral_evidence(repository, *, action_store=None):
    """Read bounded local action files; never call GitHub or mutate lifecycle state."""
    repository = canonical_repo(repository)
    store = action_store or ActionStore(_state_root())
    root = store.root
    if root.is_symlink():
        return {"status": "inconclusive", "actions": []}
    if not root.exists():
        return {"status": "complete", "actions": []}
    if not root.is_dir():
        return {"status": "inconclusive", "actions": []}
    actions = []
    try:
        paths = list(itertools.islice(root.glob("*.json"), MAX_EVIDENCE_ACTIONS + 1))
        if len(paths) > MAX_EVIDENCE_ACTIONS:
            return {"status": "inconclusive", "actions": []}
        for path in paths:
            if path.is_symlink():
                return {"status": "inconclusive", "actions": []}
            action = store.load(validate_action_id(path.stem))
            if action.repository.casefold() != repository.casefold():
                continue
            if action.action_state not in {state.value for state in LifecycleState}:
                return {"status": "inconclusive", "actions": []}
            if action.action_state == LifecycleState.CLEANED.value:
                continue
            actions.append({"action_id": action.action_id, "state": action.action_state,
                            "labels": label_list(action.labels),
                            "updated_at": action.updated_at,
                            "workload_observed": action.workload_evidence.get("observed") is True,
                            "terminal_proven": action.terminal_evidence.get("proven") is True})
    except (OSError, ValueError, AuditError, KeyError, TypeError):
        return {"status": "inconclusive", "actions": []}
    return {"status": "complete", "actions": sorted(actions, key=lambda row: row["action_id"])}


def lifecycle_reference(action, *, repository=None, action_store=None):
    """Project current exact lifecycle evidence for autoscale audit readers."""
    if action.get("kind") != "CREATE_EPHEMERAL":
        return None
    try:
        exact_id = validate_action_id(action["target"])
    except (ValueError, AttributeError, KeyError):
        return {"status": "inconclusive", "action_id": None}
    store = action_store or ActionStore(_state_root())
    try:
        observed = store.load(exact_id)
    except FileNotFoundError:
        return {"status": "missing", "action_id": exact_id}
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "inconclusive", "action_id": exact_id}
    if (observed.action_id != exact_id
            or observed.runner_identity != runner_identity(exact_id)
            or (repository and observed.repository.casefold() != repository.casefold())):
        return {"status": "inconclusive", "action_id": exact_id}
    return {"status": "complete", "action_id": exact_id,
            "runner_identity": observed.runner_identity,
            "state": observed.action_state,
            "updated_at": observed.updated_at,
            "workload_observed": observed.workload_evidence.get("observed") is True,
            "terminal_proven": observed.terminal_evidence.get("proven") is True,
            "cleanup_result": observed.cleanup.get("result")}
