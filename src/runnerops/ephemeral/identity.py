#!/usr/bin/env python3
"""Exact identity and disposable-root derivation."""

import hashlib
import re
import uuid
from pathlib import Path


ACTION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
OWNER_MARKER = ".runnerops-ephemeral-owner.json"


def new_action_id() -> str:
    return uuid.uuid4().hex


def validate_action_id(action_id: str) -> str:
    normalized = action_id.strip().lower()
    if not ACTION_ID_RE.fullmatch(normalized):
        raise ValueError("action_id must be exactly 32 lowercase hexadecimal characters")
    return normalized


def runner_identity(action_id: str) -> str:
    exact = validate_action_id(action_id)
    suffix = hashlib.sha256(exact.encode("ascii")).hexdigest()[:16]
    return "runnerops-ephemeral-{}".format(suffix)


def disposable_root(ephemeral_root: Path, action_id: str) -> Path:
    exact = validate_action_id(action_id)
    return ephemeral_root / exact
