#!/usr/bin/env python3
"""Concise read-only repository overview for RunnerOps public evidence."""

import argparse
import re
import sys

import capacity
from autoscale_planner import (
    PolicyError,
    collect_host_facts,
    load_audit_evidence,
    load_policy,
    plan,
)


REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"


def _value(value, unknown="unknown"):
    return unknown if value is None else value


def _render_runner(runner):
    github = runner.get("github") or {}
    local = runner.get("local") or {}
    return (
        f"  {runner.get('name')}: {runner.get('category')}; "
        f"local={local.get('state', 'unknown')}; "
        f"github={github.get('status', 'unknown')}; "
        f"busy={github.get('busy')}; reason={runner.get('reason')}"
    )


def render(snapshot, autoscale):
    repo = snapshot["repository"]
    repo_name = repo["nameWithOwner"] or repo["match_key"] or "unknown"
    queue = snapshot["queue"]
    counts = snapshot["capacity"]["counts"]

    print(f"Overview: {repo_name} ({snapshot['status']})")
    print(f"Repository: requested={repo['requested']} match={_value(repo['match_key'])}")
    print(
        "Queue: status={} queued={} observed={} oldest_matching={}".format(
            queue["status"],
            _value(queue["queued_job_count"]),
            queue["observed_queued_job_count"],
            _value(queue["oldest_matching_queued_job_id"]),
        )
    )
    print(
        "Matching capacity: available_now={} busy_capacity={} provisioned_idle={} inconclusive={}".format(
            counts["available_now"],
            counts["busy_capacity"],
            counts["provisioned_idle"],
            counts["inconclusive"],
        )
    )
    print("Runners:")
    runners = snapshot["capacity"]["runners"]
    if runners:
        for runner in runners:
            print(_render_runner(runner))
    else:
        print("  none observed")

    if autoscale["status"] == "error":
        print(f"Autoscale: status=error reason={autoscale['error']}")
    else:
        action = autoscale.get("action") or {}
        action_text = "none"
        if action:
            action_text = f"{action.get('kind')} target={action.get('target')}"
        print(
            "Autoscale: decision={} status={} reasons={} action={}".format(
                autoscale.get("decision"),
                autoscale.get("status"),
                ",".join(autoscale.get("reason_codes", [])) or "none",
                action_text,
            )
        )

    for error in snapshot["errors"]:
        print(f"Evidence: {error['source']}={error['reason']}")
    print(
        "Read-only overview: no lifecycle, provisioning, registration, systemd, or audit-store mutation was performed."
    )


def build_overview(repository):
    snapshot = capacity.snapshot(repository)
    try:
        policy = load_policy()
    except PolicyError as exc:
        return snapshot, {"status": "error", "error": exc.code}

    canonical = snapshot.get("repository", {}).get("nameWithOwner")
    audit = (
        load_audit_evidence(canonical)
        if canonical
        else {
            "status": "inconclusive",
            "error": "canonical_identity_unavailable",
            "queue": [],
            "active_burst_capacity": None,
            "last_scaling_action_started_at": None,
        }
    )
    autoscale = plan(snapshot, policy, collect_host_facts(policy), audit)
    return snapshot, autoscale


def main():
    parser = argparse.ArgumentParser(prog="runnerctl overview", description=__doc__)
    parser.add_argument("repository", nargs="?", default=".")
    args = parser.parse_args()
    if args.repository != "." and not re.fullmatch(REPO_PATTERN, args.repository):
        parser.error("expected owner/repo or .")

    snapshot, autoscale = build_overview(args.repository)
    render(snapshot, autoscale)
    if autoscale["status"] == "error":
        return 2
    return 3 if snapshot["status"] == "inconclusive" or autoscale.get("decision") == "INCONCLUSIVE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
