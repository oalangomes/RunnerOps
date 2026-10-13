"""Read-only workflow-to-autoscale policy evidence reconciliation.

The runtime policy-management contract is tracked by issue #144. This module
does not pretend that unshipped policy apply/show commands exist.
"""
import json
from pathlib import Path
import re

from .discovery import discover

MAX_POLICY_BYTES = 128 * 1024


def _labels(values):
    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
        raise ValueError("INVALID_POLICY_LABELS")
    return set(x.casefold() for x in values)


def read_policy(path):
    if path is None:
        return None
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_POLICY_BYTES:
        raise ValueError("INVALID_POLICY_EVIDENCE_FILE")
    value = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("UNSUPPORTED_POLICY_EVIDENCE_SCHEMA")
    policy = value.get("policy")
    if not isinstance(policy, dict) or "label_scope" not in policy:
        raise ValueError("POLICY_EVIDENCE_MISSING_SCOPE")
    _labels(policy["label_scope"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value.get("repository", "")):
        raise ValueError("INVALID_POLICY_REPOSITORY")
    return value


def reconcile(root, policy_evidence=None, repository=None):
    audit = discover(root)
    policy = read_policy(policy_evidence)
    if policy is not None and repository is not None and policy["repository"].casefold() != repository.casefold():
        raise ValueError("POLICY_REPOSITORY_MISMATCH")
    identity_verified = policy is not None and repository is not None
    jobs = []
    for workflow in audit["workflows"]:
        for job in workflow["jobs"]:
            entry = {"file": workflow["path"], "job": job["id"],
                     "runner_kind": job["runner_kind"], "required_labels": job["labels"]}
            if job["reusable"]:
                entry.update(status="unknown", reason="REUSABLE_WORKFLOW")
            elif job["runner_kind"] == "hosted":
                entry.update(status="hosted", reason="GITHUB_HOSTED_NOT_MANAGED")
            elif job["runner_kind"] != "self-hosted":
                entry.update(status="unknown", reason="DYNAMIC_OR_UNKNOWN_RUNS_ON")
            elif policy is None:
                entry.update(status="unknown", reason="POLICY_EVIDENCE_MISSING")
            elif not identity_verified:
                entry.update(status="unknown", reason="POLICY_REPOSITORY_NOT_VERIFIED")
            else:
                required = _labels(job["labels"])
                policy_labels = _labels(policy["policy"]["label_scope"])
                if not policy_labels.issubset(required):
                    entry.update(status="blocked", reason="LABEL_SCOPE_BLOCKED")
                else:
                    candidates = []
                    for kind, config in (
                        ("local_provision", policy["policy"].get("local_provision", {})),
                        ("local_ephemeral", policy["policy"].get("local_ephemeral", {})),
                    ):
                        if isinstance(config, dict) and config.get("enabled") is True:
                            offered = _labels(config.get("labels", []))
                            if required.issubset(offered):
                                candidates.append(kind)
                    if candidates:
                        entry.update(status="compatible", reason="LABELS_COMPATIBLE",
                                     policy_paths=candidates)
                    else:
                        entry.update(status="unknown", reason="NO_VERIFIED_PROVISIONING_PATH")
            jobs.append(entry)
    statuses = {x["status"] for x in jobs}
    return {
        "schema_version": 1, "kind": "WorkflowPolicyReconciliation",
        "fingerprint": audit["fingerprint"],
        "policy_source": "operator_supplied_file" if policy is not None else "unavailable",
        "identity_verified": identity_verified,
        "policy_fingerprint": policy.get("fingerprint") if policy is not None else None,
        "policy_repository": policy.get("repository") if policy is not None else None,
        "status": ("blocked" if "blocked" in statuses else
                   "inconclusive" if "unknown" in statuses or audit["status"] != "complete" else "complete"),
        "jobs": jobs,
        "next_action": ("Obtain an effective policy through public RunnerOps contracts; "
                        "policy show/plan/apply is tracked in #144. Never edit scheduler units manually."
                        if policy is None else
                        "Review differences with autoscale plan/status. Policy mutation requires #144 public approval boundary."),
        "mutation": False,
    }
