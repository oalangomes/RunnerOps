#!/usr/bin/env python3
"""Public runnerctl-backed CLI for the local ephemeral primitive."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from .contracts import EphemeralAction, LifecycleState
from .identity import new_action_id
from .lifecycle import CleanupRefused, EphemeralLifecycle, ReconcileRequired
from .runtime import GitHubRuntime, LocalRuntime
from .store import ActionStore


SUCCESS_STATES = {
    LifecycleState.ONLINE.value,
    LifecycleState.BUSY.value,
    LifecycleState.TERMINAL.value,
    LifecycleState.CLEANED.value,
    LifecycleState.REQUESTED.value,
}


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _labels(value: str) -> Iterable[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _runtime() -> EphemeralLifecycle:
    data_root = Path(os.environ.get("RUNNER_DATA_ROOT", str(Path.home() / ".local/share/actions-runners/runners")))
    state_root = Path(os.environ.get("RUNNER_STATE_ROOT", str(Path.home() / ".local/state/actions-runners")))
    ephemeral_root = Path(os.environ.get("RUNNER_EPHEMERAL_ROOT", str(data_root / ".ephemeral")))
    scripts_root = Path(os.environ.get("RUNNEROPS_SCRIPTS_ROOT", Path(__file__).resolve().parents[3] / "scripts"))
    local = LocalRuntime(
        ephemeral_root,
        scripts_root / "runner" / "package.sh",
        scripts_root / "runner" / "ephemeral.sh",
        command_timeout=float(os.environ.get("RUNNER_EPHEMERAL_COMMAND_TIMEOUT_SECONDS", "120")),
    )
    github = GitHubRuntime(timeout=float(os.environ.get("RUNNER_EPHEMERAL_GITHUB_TIMEOUT_SECONDS", "30")))
    return EphemeralLifecycle(ActionStore(state_root), local, github)


def _emit(action: EphemeralAction, as_json: bool) -> None:
    if as_json:
        print(json.dumps(action.to_dict(), sort_keys=True))
        return
    print("Ephemeral action: {}".format(action.action_id))
    print("  state: {}".format(action.action_state))
    print("  runner: {}".format(action.runner_identity))
    print("  repository: {}".format(action.repository))
    print("  disposable root: {}".format(action.disposable_root))
    print("  local: {} ({})".format(
        action.local_observation.get("status"), action.local_observation.get("reason")))
    print("  GitHub: {} ({})".format(
        action.github_observation.get("status"), action.github_observation.get("reason")))
    print("  workload observed: {}".format(str(action.workload_evidence.get("observed", False)).lower()))
    print("  terminal proven: {}".format(str(action.terminal_evidence.get("proven", False)).lower()))
    print("  cleanup: {}".format(action.cleanup.get("result")))
    print("  reason: {}".format(action.final_reason or "none"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runnerctl ephemeral")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="allocate and start one exact ephemeral runner")
    create.add_argument("repository")
    create.add_argument("--profile", required=True)
    create.add_argument("--labels", required=True)
    create.add_argument("--action-id")
    create.add_argument("--runner-version", default="latest")
    create.add_argument("--runner-arch", choices=("auto", "x64", "arm64"), default="auto")
    create.add_argument("--online-timeout", type=_positive_float, default=30.0)
    create.add_argument("--observation-interval", type=_positive_float, default=1.0)
    create.add_argument("--json", action="store_true")

    for name in ("status", "reconcile", "cleanup"):
        command = subparsers.add_parser(name)
        command.add_argument("action_id")
        command.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    lifecycle = _runtime()
    action_id = None
    try:
        if args.command == "create":
            action_id = args.action_id or new_action_id()
            action = lifecycle.create(
                args.repository,
                args.profile,
                _labels(args.labels),
                action_id=action_id,
                runner_version=args.runner_version,
                runner_arch=args.runner_arch,
                online_timeout=args.online_timeout,
                observation_interval=args.observation_interval,
            )
            _emit(action, args.json)
            return 0 if action.action_state in (LifecycleState.ONLINE.value, LifecycleState.BUSY.value) else 2
        action_id = args.action_id
        if args.command == "status":
            action = lifecycle.status(action_id)
            _emit(action, args.json)
            return 0
        if args.command == "reconcile":
            action = lifecycle.reconcile(action_id)
            _emit(action, args.json)
            return 0 if action.action_state in SUCCESS_STATES else 2
        action = lifecycle.cleanup(action_id)
        _emit(action, args.json)
        return 0 if action.action_state == LifecycleState.CLEANED.value else 2
    except (CleanupRefused, ReconcileRequired, RuntimeError, ValueError, OSError) as error:
        if action_id and lifecycle.store.exists(action_id):
            _emit(lifecycle.store.load(action_id), getattr(args, "json", False))
        print("ERRO: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
