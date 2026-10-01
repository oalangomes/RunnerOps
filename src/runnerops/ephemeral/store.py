#!/usr/bin/env python3
"""Atomic per-action evidence store, separate from autoscale audit schemas."""

import json
import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .contracts import EphemeralAction
from .duration import finite_positive_duration
from .identity import validate_action_id


_SENSITIVE_KEY_PARTS = ("token", "credential", "password", "secret")


class ActionLocked(RuntimeError):
    pass


def _reject_secret_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                raise ValueError("secret-bearing key is forbidden in lifecycle evidence: {}.{}".format(path, key))
            _reject_secret_keys(item, "{}.{}".format(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, "{}[{}]".format(path, index))


class ActionStore:
    def __init__(self, state_root: Path):
        self.root = Path(state_root) / "ephemeral" / "actions"

    def path_for(self, action_id: str) -> Path:
        return self.root / "{}.json".format(validate_action_id(action_id))

    def exists(self, action_id: str) -> bool:
        return self.path_for(action_id).is_file()

    @contextmanager
    def lock(self, action_id: str, timeout: float = 5.0):
        exact_id = validate_action_id(action_id)
        timeout = finite_positive_duration(timeout, "action lock timeout")
        lock_root = self.root.parent / "locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if lock_root.is_symlink():
            raise ValueError("ephemeral action lock store cannot be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(lock_root / "{}.lock".format(exact_id)), flags, 0o600)
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ActionLocked("ephemeral action is already being operated: {}".format(exact_id))
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def load(self, action_id: str) -> EphemeralAction:
        path = self.path_for(action_id)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("ephemeral action not found: {}".format(action_id))
        return EphemeralAction.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, action: EphemeralAction) -> Path:
        payload = action.to_dict()
        _reject_secret_keys(payload)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError("ephemeral action store cannot be a symlink")
        path = self.path_for(action.action_id)
        descriptor, temporary = tempfile.mkstemp(prefix=".{}-".format(action.action_id), dir=str(self.root))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path
