#!/usr/bin/env python3
"""Durable, non-secret contracts for an ephemeral runner action."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


class LifecycleState(str, Enum):
    REQUESTED = "REQUESTED"
    REGISTERING = "REGISTERING"
    REGISTERED = "REGISTERED"
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    TERMINAL = "TERMINAL"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    CLEANED = "CLEANED"
    INCONCLUSIVE_REGISTRATION = "INCONCLUSIVE_REGISTRATION"
    INCONCLUSIVE_ONLINE = "INCONCLUSIVE_ONLINE"
    INCONCLUSIVE_TERMINAL = "INCONCLUSIVE_TERMINAL"


@dataclass
class EphemeralAction:
    action_id: str
    runner_identity: str
    disposable_root: str
    repository: str
    profile: str
    labels: List[str]
    created_at: str
    updated_at: str
    action_state: str = LifecycleState.REQUESTED.value
    desired_state: str = "ONE_JOB_TERMINAL_AND_CLEANED"
    schema_version: int = SCHEMA_VERSION
    kind: str = "EphemeralAction"
    registration: Dict[str, Any] = field(default_factory=lambda: {
        "attempted": False,
        "attempts": 0,
        "last_attempt_at": None,
        "configured": False,
        "uncertainty": False,
        "reconcile_required": False,
        "safe_retry_authorized": False,
        "consecutive_absence_observations": 0,
        "first_absence_observed_at": None,
        "last_absence_observed_at": None,
    })
    local_observation: Dict[str, Any] = field(default_factory=lambda: {
        "observed_at": None,
        "status": "UNKNOWN",
        "systemd_unit": None,
        "active_state": None,
        "sub_state": None,
        "service_result": None,
        "main_pid": None,
        "started_at_monotonic": None,
        "start_observed": False,
        "config_present": False,
        "config_identity": None,
        "config_runner_id": None,
        "reason": "not_observed",
    })
    github_observation: Dict[str, Any] = field(default_factory=lambda: {
        "observed_at": None,
        "status": "UNKNOWN",
        "runner_id": None,
        "ephemeral": None,
        "remote_status": None,
        "busy": None,
        "reason": "not_observed",
    })
    workload_evidence: Dict[str, Any] = field(default_factory=lambda: {
        "observed": False,
        "first_busy_at": None,
        "last_busy_at": None,
        "job_conclusion": "unknown",
    })
    terminal_evidence: Dict[str, Any] = field(default_factory=lambda: {
        "proven": False,
        "observed_at": None,
        "reason": "not_observed",
        "job_conclusion": "unknown",
    })
    cleanup: Dict[str, Any] = field(default_factory=lambda: {
        "attempts": 0,
        "last_attempt_at": None,
        "remote_removed": False,
        "local_stopped": False,
        "root_removed": False,
        "completed_at": None,
        "result": "NOT_ATTEMPTED",
        "reason": "not_attempted",
    })
    transitions: List[Dict[str, Any]] = field(default_factory=list)
    final_reason: Optional[str] = None

    def transition(self, state: LifecycleState, at: str, reason: str) -> None:
        previous = self.action_state
        if previous == LifecycleState.CLEANED.value and state != LifecycleState.CLEANED:
            raise ValueError("CLEANED is a convergent terminal lifecycle state")
        self.action_state = state.value
        self.updated_at = at
        self.final_reason = reason
        self.transitions.append({
            "from": previous,
            "to": state.value,
            "at": at,
            "reason": reason,
        })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "EphemeralAction":
        if value.get("schema_version") != SCHEMA_VERSION or value.get("kind") != "EphemeralAction":
            raise ValueError("unsupported ephemeral action contract")
        fields = cls.__dataclass_fields__
        action = cls(**{key: item for key, item in value.items() if key in fields})
        for name in (
            "registration", "local_observation", "github_observation",
            "workload_evidence", "terminal_evidence", "cleanup",
        ):
            defaults = fields[name].default_factory()
            defaults.update(getattr(action, name))
            setattr(action, name, defaults)
        return action
