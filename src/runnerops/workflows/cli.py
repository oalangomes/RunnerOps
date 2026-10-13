"""Public runnerctl workflow command family."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .adoption import apply, plan
from .discovery import discover
from .intelligence import analyze, live, read_evidence
from .reconciliation import reconcile


def _emit(value, json_output):
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="runnerctl workflow", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("discover", help="read-only offline audit")
    audit.add_argument("path", nargs="?", default=".")
    audit.add_argument("--json", action="store_true")

    adoption = commands.add_parser("adopt", help="preview or explicitly apply supported runner label changes")
    adoption.add_argument("path", nargs="?", default=".")
    adoption.add_argument("--profile", choices=("hosted", "local-first"), required=True)
    operation = adoption.add_mutually_exclusive_group(required=True)
    operation.add_argument("--plan", action="store_true")
    operation.add_argument("--apply", action="store_true")
    adoption.add_argument("--expect-fingerprint")
    adoption.add_argument("--yes", action="store_true")
    adoption.add_argument("--json", action="store_true")

    policy = commands.add_parser("reconcile", help="compare jobs with operator-supplied autoscale policy evidence")
    policy.add_argument("path", nargs="?", default=".")
    policy.add_argument("--policy-evidence")
    policy.add_argument("--json", action="store_true")

    intel = commands.add_parser("intelligence", help="bounded read-only CI operational evidence analysis")
    intel.add_argument("repository", nargs="?", default=".")
    intel.add_argument("--evidence")
    intel.add_argument("--since", default="24h")
    intel.add_argument("--provider", choices=("ollama", "litellm"))
    intel.add_argument("--model")
    intel.add_argument("--allow-remote", action="store_true")
    intel.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            result = discover(args.path)
        elif args.command == "adopt":
            if args.apply:
                result = apply(args.path, args.profile, args.expect_fingerprint, confirmed=args.yes)
            else:
                result = plan(args.path, args.profile)
        elif args.command == "reconcile":
            result = reconcile(args.path, args.policy_evidence)
        else:
            if (args.model is None) != (args.provider is None):
                raise ValueError("PROVIDER_AND_MODEL_MUST_BE_SET_TOGETHER")
            evidence = read_evidence(args.evidence) if args.evidence else live(args.repository, args.since)
            result = analyze(evidence)
            if args.provider:
                # Delegate all security, schema and model-response validation to the
                # existing public review interface. It alone may contact a model.
                import os
                import tempfile
                with tempfile.TemporaryDirectory(prefix="runnerops-review-") as directory:
                    path = Path(directory) / "evidence.json"
                    path.write_text(json.dumps(evidence), encoding="utf-8")
                    os.chmod(path, 0o600)
                    cli = [os.environ.get("RUNNERCTL_EXECUTABLE", "runnerctl"),
                           "review", "--evidence", str(path), "--provider", args.provider,
                           "--model", args.model, "--json"]
                    if args.allow_remote:
                        cli.append("--allow-remote")
                    completed = subprocess.run(cli, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, timeout=180, check=False)
                    if completed.returncode:
                        raise ValueError("AI_REVIEW_UNAVAILABLE_OR_INCONCLUSIVE")
                    result["review"] = json.loads(completed.stdout)
                    result["model_used"] = True
        _emit(result, args.json)
        return 0 if result["status"] in ("complete", "ready", "applied") else 3
    except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        _emit({"schema_version": 1, "kind": "WorkflowCommandError", "status": "error",
               "reason": str(exc).splitlines()[0][:160]}, getattr(args, "json", False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
