#!/usr/bin/env python3
"""Evidence-driven create, observe, reconcile and cleanup lifecycle."""

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional

from .contracts import EphemeralAction, LifecycleState
from .identity import disposable_root, new_action_id, runner_identity, validate_action_id
from .store import ActionStore


class ReconcileRequired(RuntimeError):
    pass


class CleanupRefused(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EphemeralLifecycle:
    def __init__(
        self,
        store: ActionStore,
        local_runtime: Any,
        github_runtime: Any,
        clock: Callable[[], str] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.store = store
        self.local = local_runtime
        self.github = github_runtime
        self.clock = clock
        self.monotonic = monotonic
        self.sleeper = sleeper

    def _save_transition(self, action: EphemeralAction, state: LifecycleState, reason: str) -> None:
        action.transition(state, self.clock(), reason)
        self.store.save(action)

    def _new_action(
        self, repository: str, profile: str, labels: Iterable[str], action_id: Optional[str]
    ) -> EphemeralAction:
        exact_id = validate_action_id(action_id) if action_id else new_action_id()
        identity = runner_identity(exact_id)
        root = disposable_root(self.local.ephemeral_root, exact_id)
        now = self.clock()
        normalized_labels = []
        for label in labels:
            clean = label.strip()
            if clean and clean.lower() not in {item.lower() for item in normalized_labels}:
                normalized_labels.append(clean)
        if identity.lower() not in {item.lower() for item in normalized_labels}:
            normalized_labels.append(identity)
        action = EphemeralAction(
            action_id=exact_id,
            runner_identity=identity,
            disposable_root=str(root),
            repository=repository,
            profile=profile,
            labels=normalized_labels,
            created_at=now,
            updated_at=now,
        )
        action.transitions.append({"from": None, "to": LifecycleState.REQUESTED.value,
                                   "at": now, "reason": "create_requested"})
        self.store.save(action)
        return action

    def create(
        self,
        repository: str,
        profile: str,
        labels: Iterable[str],
        action_id: Optional[str] = None,
        runner_version: str = "latest",
        runner_arch: str = "auto",
        online_timeout: float = 30.0,
        observation_interval: float = 1.0,
    ) -> EphemeralAction:
        exact_id = validate_action_id(action_id) if action_id else new_action_id()
        with self.store.lock(exact_id):
            return self._create_locked(
                repository, profile, labels, action_id=exact_id,
                runner_version=runner_version, runner_arch=runner_arch,
                online_timeout=online_timeout, observation_interval=observation_interval,
            )

    def _create_locked(
        self,
        repository: str,
        profile: str,
        labels: Iterable[str],
        action_id: str,
        runner_version: str,
        runner_arch: str,
        online_timeout: float,
        observation_interval: float,
    ) -> EphemeralAction:
        if action_id and self.store.exists(action_id):
            action = self.store.load(action_id)
            if action.repository != repository or action.profile != profile:
                raise ReconcileRequired("existing action spec does not match retry request")
            resumable_before_remote = (
                action.action_state == LifecycleState.REQUESTED.value
                and not action.registration.get("attempted")
            )
            if action.registration.get("reconcile_required"):
                raise ReconcileRequired("registration outcome is inconclusive; reconcile exact identity first")
            if not resumable_before_remote and not action.registration.get("safe_retry_authorized"):
                raise ReconcileRequired("action already exists and has not been reconciled for retry")
            action.registration["safe_retry_authorized"] = False
            self.store.save(action)
        else:
            action = self._new_action(repository, profile, labels, action_id)

        try:
            self.local.preflight(action)
            package = self.local.ensure_package(runner_version, runner_arch)
            self.local.allocate_root(action)
            self.local.materialize(action, package)
        except Exception:
            action.terminal_evidence.update({
                "proven": True, "observed_at": self.clock(),
                "reason": "failure_before_remote_mutation", "job_conclusion": "not_started",
            })
            self._save_transition(action, LifecycleState.TERMINAL, "local_setup_failed_before_remote_mutation")
            raise

        remote = self._observe_github(action)
        if remote["status"] != "ABSENT":
            action.registration.update({"uncertainty": True, "reconcile_required": True,
                                        "safe_retry_authorized": False,
                                        "consecutive_absence_observations": 0})
            self._save_transition(action, LifecycleState.INCONCLUSIVE_REGISTRATION,
                                  "exact_identity_not_proven_absent_before_registration")
            raise ReconcileRequired("exact runner identity is not safely absent; reconcile before registration")

        try:
            registration_material = self.github.request_registration_material(repository)
        except Exception:
            action.terminal_evidence.update({
                "proven": True, "observed_at": self.clock(),
                "reason": "registration_material_failed_before_remote_mutation",
                "job_conclusion": "not_started",
            })
            self._save_transition(action, LifecycleState.TERMINAL,
                                  "registration_material_failed_before_remote_mutation")
            raise

        action.registration["attempted"] = True
        action.registration["attempts"] += 1
        action.registration["last_attempt_at"] = self.clock()
        self._save_transition(action, LifecycleState.REGISTERING, "registration_initiated")
        try:
            self.local.configure(action, registration_material)
        except Exception:
            action.registration.update({"configured": False, "uncertainty": True,
                                        "reconcile_required": True, "safe_retry_authorized": False})
            self._save_transition(action, LifecycleState.INCONCLUSIVE_REGISTRATION,
                                  "registration_result_unknown")
            raise
        finally:
            registration_material = None

        action.registration.update({"configured": True, "uncertainty": False,
                                    "reconcile_required": False, "safe_retry_authorized": False,
                                    "consecutive_absence_observations": 0})
        self._save_transition(action, LifecycleState.REGISTERED, "local_configuration_succeeded")
        try:
            process = self.local.start(action)
            action.local_observation.update(process)
            action.local_observation.update({"config_present": True, "observed_at": self.clock(),
                                             "reason": "exact_systemd_unit_started"})
            self.store.save(action)
        except Exception:
            self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                  "registered_but_local_start_failed")
            raise

        deadline = self.monotonic() + max(0.0, online_timeout)
        while True:
            self._record_observations(action)
            remote_status = action.github_observation["status"]
            if remote_status == "BUSY":
                self._save_transition(action, LifecycleState.BUSY, "exact_runner_busy")
                return action
            if remote_status == "ONLINE":
                self._save_transition(action, LifecycleState.ONLINE, "exact_runner_online")
                return action
            if self.monotonic() >= deadline:
                self._save_transition(action, LifecycleState.INCONCLUSIVE_ONLINE,
                                      "exact_runner_not_online_within_bound")
                return action
            self.sleeper(observation_interval)

    def _observe_github(self, action: EphemeralAction) -> Dict[str, Any]:
        try:
            observation = self.github.observe_runner(action.repository, action.runner_identity)
        except Exception:
            observation = {"status": "UNKNOWN", "runner_id": None, "remote_status": None,
                           "busy": None, "reason": "github_observation_failed"}
        observation["observed_at"] = self.clock()
        action.github_observation = observation
        return observation

    def _record_observations(self, action: EphemeralAction) -> None:
        try:
            local = self.local.observe(action)
        except Exception:
            local = {"status": "UNKNOWN",
                     "systemd_unit": action.local_observation.get("systemd_unit"),
                     "active_state": action.local_observation.get("active_state"),
                     "sub_state": action.local_observation.get("sub_state"),
                     "service_result": action.local_observation.get("service_result"),
                     "main_pid": action.local_observation.get("main_pid"),
                     "started_at_monotonic": action.local_observation.get("started_at_monotonic"),
                     "start_observed": action.local_observation.get("start_observed", False),
                     "config_present": False, "config_identity": None,
                     "reason": "local_observation_failed"}
        local["observed_at"] = self.clock()
        action.local_observation = local
        remote = self._observe_github(action)
        if remote["status"] == "BUSY":
            now = self.clock()
            action.workload_evidence["observed"] = True
            action.workload_evidence["first_busy_at"] = action.workload_evidence.get("first_busy_at") or now
            action.workload_evidence["last_busy_at"] = now
        action.updated_at = self.clock()
        self.store.save(action)

    def status(self, action_id: str) -> EphemeralAction:
        with self.store.lock(action_id):
            action = self.store.load(action_id)
            self._record_observations(action)
            return action

    def reconcile(self, action_id: str) -> EphemeralAction:
        with self.store.lock(action_id):
            return self._reconcile_locked(action_id)

    def _reconcile_locked(self, action_id: str) -> EphemeralAction:
        action = self.store.load(action_id)
        if (action.action_state == LifecycleState.REGISTERING.value
                and action.registration.get("attempted")
                and not action.registration.get("configured")):
            action.registration.update({"uncertainty": True, "reconcile_required": True,
                                        "safe_retry_authorized": False})
            self._save_transition(action, LifecycleState.INCONCLUSIVE_REGISTRATION,
                                  "registration_interrupted_before_outcome_was_persisted")
        self._record_observations(action)
        remote = action.github_observation["status"]
        local = action.local_observation["status"]

        if remote in ("UNKNOWN", "AMBIGUOUS") or local == "UNKNOWN":
            target = (LifecycleState.INCONCLUSIVE_REGISTRATION
                      if action.registration.get("uncertainty") else LifecycleState.INCONCLUSIVE_TERMINAL)
            action.registration["reconcile_required"] = True
            self._save_transition(action, target, "observation_inconclusive")
            return action
        if action.registration.get("uncertainty") and remote in ("ONLINE", "OFFLINE", "BUSY"):
            action.registration["consecutive_absence_observations"] = 0
            if not action.local_observation.get("config_present"):
                self._save_transition(action, LifecycleState.INCONCLUSIVE_REGISTRATION,
                                      "exact_remote_identity_present_but_local_configuration_missing")
                return action
            action.registration.update({"configured": True, "uncertainty": False,
                                        "reconcile_required": False, "safe_retry_authorized": False})
            if local not in ("RUNNING", "STARTING") and remote != "BUSY":
                try:
                    process = self.local.start(action)
                    action.local_observation.update(process)
                    action.local_observation.update({"config_present": True,
                                                     "observed_at": self.clock(),
                                                     "reason": "exact_systemd_unit_started_by_reconcile"})
                    self.store.save(action)
                except Exception:
                    self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                          "reconciled_registration_but_local_start_failed")
                    return action
            if remote == "BUSY":
                self._save_transition(action, LifecycleState.BUSY,
                                      "uncertain_registration_recovered_exact_runner_busy")
            elif remote == "ONLINE":
                self._save_transition(action, LifecycleState.ONLINE,
                                      "uncertain_registration_recovered_exact_runner_online")
            else:
                self._save_transition(action, LifecycleState.REGISTERED,
                                      "uncertain_registration_recovered_exact_runner_offline")
            return action
        if (action.registration.get("configured")
                and action.action_state == LifecycleState.REGISTERED.value
                and remote in ("ONLINE", "OFFLINE")
                and action.local_observation.get("config_present")
                and local not in ("RUNNING", "STARTING")
                and not action.workload_evidence.get("observed")):
            try:
                process = self.local.start(action)
                action.local_observation.update(process)
                action.local_observation.update({"config_present": True,
                                                 "observed_at": self.clock(),
                                                 "reason": "registered_unit_started_by_reconcile"})
                self.store.save(action)
            except Exception:
                self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                      "registered_unit_restart_failed")
                return action
            target = LifecycleState.ONLINE if remote == "ONLINE" else LifecycleState.REGISTERED
            self._save_transition(action, target, "registered_unit_start_recovered")
            return action
        if remote == "BUSY":
            self._save_transition(action, LifecycleState.BUSY, "exact_runner_busy")
            return action
        if remote == "ONLINE":
            if local in ("RUNNING", "STARTING"):
                self._save_transition(action, LifecycleState.ONLINE, "exact_runner_online")
            else:
                self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                      "local_process_exit_does_not_prove_terminal")
            return action
        if remote == "OFFLINE":
            if local in ("RUNNING", "STARTING"):
                self._save_transition(action, LifecycleState.REGISTERED, "exact_runner_registered_offline")
            else:
                self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                      "local_exit_with_remote_registration_present")
            return action

        # Exact remote identity is proven absent.
        if action.registration.get("uncertainty") and not action.registration.get("configured"):
            if local in ("ALLOCATED", "ABSENT") and not action.local_observation.get("config_present"):
                absence_count = action.registration.get("consecutive_absence_observations", 0) + 1
                action.registration["consecutive_absence_observations"] = absence_count
                if absence_count >= 2:
                    action.registration.update({"uncertainty": False, "reconcile_required": False,
                                                "safe_retry_authorized": True})
                    self._save_transition(action, LifecycleState.REQUESTED,
                                          "repeated_exact_remote_absence_proves_registration_retry_safe")
                else:
                    self._save_transition(action, LifecycleState.INCONCLUSIVE_REGISTRATION,
                                          "exact_remote_absence_requires_bounded_confirmation")
            else:
                action.registration["consecutive_absence_observations"] = 0
                self._save_transition(action, LifecycleState.INCONCLUSIVE_REGISTRATION,
                                      "remote_absent_but_local_registration_artifacts_remain")
            return action

        if action.registration.get("configured"):
            if local in ("EXITED", "ABSENT", "ALLOCATED"):
                if action.workload_evidence.get("observed"):
                    action.terminal_evidence.update({
                        "proven": True,
                        "observed_at": self.clock(),
                        "reason": "workload_observed_then_local_exit_and_remote_absence",
                        "job_conclusion": "unknown",
                    })
                    self._save_transition(action, LifecycleState.TERMINAL,
                                          "one_job_lifecycle_terminal_job_conclusion_unknown")
                else:
                    self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                          "remote_absence_and_local_exit_without_workload_evidence")
            else:
                self._save_transition(action, LifecycleState.INCONCLUSIVE_TERMINAL,
                                      "remote_disappearance_does_not_prove_local_terminal")
        return action

    def cleanup(self, action_id: str) -> EphemeralAction:
        with self.store.lock(action_id):
            return self._cleanup_locked(action_id)

    def _cleanup_locked(self, action_id: str) -> EphemeralAction:
        action = self.store.load(action_id)
        if action.action_state == LifecycleState.CLEANED.value:
            return action
        self._record_observations(action)
        remote = action.github_observation
        local = action.local_observation
        if remote["status"] == "BUSY":
            action.cleanup.update({"result": "REFUSED", "reason": "runner_busy"})
            self.store.save(action)
            raise CleanupRefused("destructive cleanup refused while exact runner is busy")
        if remote["status"] in ("UNKNOWN", "AMBIGUOUS") or local["status"] == "UNKNOWN":
            action.cleanup.update({"result": "INCONCLUSIVE", "reason": "observation_inconclusive"})
            self.store.save(action)
            raise CleanupRefused("cleanup requires conclusive exact local and remote observation")

        self.local.validate_owned_root(action, allow_missing=True)
        action.cleanup["attempts"] += 1
        action.cleanup["last_attempt_at"] = self.clock()
        action.cleanup.update({"result": "IN_PROGRESS", "reason": "cleanup_started"})
        self._save_transition(action, LifecycleState.CLEANUP_PENDING, "bounded_cleanup_started")

        if remote["status"] in ("ONLINE", "OFFLINE"):
            runner_id = remote.get("runner_id")
            if runner_id is None:
                action.cleanup.update({"result": "INCONCLUSIVE", "reason": "remote_id_missing"})
                self.store.save(action)
                raise CleanupRefused("exact remote registration lacks a runner id")
            self.github.delete_runner(action.repository, runner_id)
            deadline = self.monotonic() + 10.0
            while True:
                after_delete = self._observe_github(action)
                self.store.save(action)
                if after_delete["status"] == "ABSENT":
                    break
                if after_delete["status"] in ("BUSY", "UNKNOWN", "AMBIGUOUS"):
                    action.cleanup.update({"result": "INCONCLUSIVE",
                                           "reason": "remote_delete_not_conclusively_observed"})
                    self.store.save(action)
                    raise CleanupRefused("remote deletion did not produce conclusive absence")
                if self.monotonic() >= deadline:
                    action.cleanup.update({"result": "INCONCLUSIVE",
                                           "reason": "remote_delete_observation_timeout"})
                    self.store.save(action)
                    raise CleanupRefused("exact remote identity remained after bounded delete observation")
                self.sleeper(0.5)
        action.cleanup["remote_removed"] = True

        self.local.stop(action)
        after_stop = self.local.observe(action)
        if after_stop["status"] in ("RUNNING", "STARTING", "STOPPING"):
            action.cleanup.update({"result": "INCONCLUSIVE", "reason": "local_process_still_running"})
            self.store.save(action)
            raise CleanupRefused("owned local process remained after bounded stop")
        action.cleanup["local_stopped"] = True
        self.local.remove_root(action)
        action.cleanup.update({
            "root_removed": True,
            "completed_at": self.clock(),
            "result": "SUCCEEDED",
            "reason": "exact_remote_local_and_root_cleanup_complete",
        })
        action.local_observation.update({"status": "CLEANED", "config_present": False,
                                         "observed_at": self.clock(), "reason": "root_removed"})
        self._save_transition(action, LifecycleState.CLEANED, "bounded_cleanup_complete")
        return action
