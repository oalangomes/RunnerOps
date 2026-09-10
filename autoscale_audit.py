#!/usr/bin/env python3
"""Read-only history/explain CLI. Persistence is an internal Python API."""

import argparse
import json
import re
import sys
from datetime import timedelta

from autoscale_contracts import AuditError, utcnow


def duration(value):
    match = re.fullmatch(r"([1-9][0-9]{0,5})([smhd])", value)
    if not match:
        raise argparse.ArgumentTypeError("use a positive duration such as 30m, 24h or 7d")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if seconds > 3650 * 86400:
        raise argparse.ArgumentTypeError("duration exceeds 3650d")
    return seconds


def render(result):
    if result["kind"] == "AutoscaleHistory":
        print(f"Autoscale history: since={result['since'] or 'retained history'}")
        for decision in result["decisions"]:
            print(
                f"  {decision['timestamp']} {decision['decision_id']} {decision['repository']}:"
                f" {decision['decision']} reasons={','.join(decision['reason_codes'])}"
            )
        for observation in result["queue_observations"]:
            print(
                f"  {observation['repository']} job={observation['job_id']} run={observation['run_id']}"
                f" attempt={observation['run_attempt']} observed={observation['observed_queued_seconds']}s"
                f" continuous={str(observation['continuous_queued']).lower()} end={observation['end_reason'] or '-'}"
            )
        if not result["decisions"] and not result["queue_observations"]:
            print("  No retained records in this interval.")
        if result["truncated"]:
            print(
                f"  Results limited to {result['limit']} per section; narrow --since or use the internal reader API."
            )
    else:
        decision = result["decision"]
        print(
            f"Decision {decision['decision_id']}: {decision['decision']} repo={decision['repository']}"
        )
        print(f"  At: {decision['timestamp']} policy={decision['policy_fingerprint']}")
        print(
            f"  Reasons: {', '.join(decision['reason_codes'])}; requested delta={decision['requested_capacity_delta']}"
        )
        print(f"  Evidence: {json.dumps(decision['evidence'], sort_keys=True)}")
        for action in result["actions"]:
            print(
                f"  Action {action['action_id']}: {action['kind']} target={action['target']} state={action['state']}"
            )
            for event in action["events"]:
                print(
                    f"    {event['timestamp']} {event['state']} diagnostic={json.dumps(event['diagnostic'])}"
                )


def main():
    parser = argparse.ArgumentParser(prog="runnerctl autoscale")
    commands = parser.add_subparsers(dest="command", required=True)
    history = commands.add_parser("history", help="Read retained queue observations and decisions")
    history.add_argument("--since", type=duration)
    explain = commands.add_parser(
        "explain", help="Read a decision, its evidence and action outcomes"
    )
    explain.add_argument("--decision", required=True)
    for command in (history, explain):
        command.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        try:
            from autoscale_store import AuditStore
        except ImportError:
            raise AuditError("sqlite_capability_unavailable") from None
        with AuditStore() as store:
            if args.command == "history":
                since = (
                    (utcnow() - timedelta(seconds=args.since)).isoformat() if args.since else None
                )
                result = store.history(since)
            else:
                result = store.explain(args.decision)
    except AuditError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "AutoscaleAuditError",
                        "status": "error",
                        "error": exc.code,
                    }
                )
            )
        else:
            print(f"Autoscale audit: {exc.code}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        render(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
