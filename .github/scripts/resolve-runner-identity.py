#!/usr/bin/env python3
"""Resolve the RunnerOps local id for the self-hosted runner executing this job."""

import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--github-runner-name", required=True)
    return parser.parse_args()


def resolve_local_runner(registry, workspace, github_runner_name):
    config_path = Path(registry)
    workspace_path = Path(workspace).resolve()
    remote_name = github_runner_name.casefold()
    matches = []

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().replace("\r", "")
        if not line or line.startswith("#"):
            continue

        fields = [value.strip() for value in line.split("|")]
        if len(fields) < 2 or not fields[0] or not fields[1]:
            raise SystemExit("invalid runners.conf entry while resolving current runner")

        local_name, raw_path = fields[0], fields[1]
        runner_path = Path(raw_path).expanduser().resolve()
        work_root = (runner_path / "_work").resolve()
        try:
            workspace_path.relative_to(work_root)
        except ValueError:
            continue

        registration_path = runner_path / ".runner"
        try:
            # The official GitHub Actions runner may write .runner with a UTF-8
            # BOM. utf-8-sig consumes that BOM while remaining compatible with
            # ordinary UTF-8 metadata.
            registration = json.loads(registration_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"cannot read runner registration metadata for {local_name}: {exc}"
            ) from None

        if str(registration.get("agentName", "")).casefold() == remote_name:
            matches.append(local_name)

    if len(matches) != 1:
        raise SystemExit(
            "expected exactly one local RunnerOps execution identity for "
            f"{github_runner_name!r}; found {len(matches)}"
        )

    return matches[0]


def main():
    args = parse_args()
    print(resolve_local_runner(args.registry, args.workspace, args.github_runner_name))


if __name__ == "__main__":
    main()
