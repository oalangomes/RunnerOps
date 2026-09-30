#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/release-version.py"


def load_module():
    spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_root() -> Path:
    temp = Path(tempfile.mkdtemp(prefix="runnerops-release-test-"))
    for relative in (
        "runnerctl",
        "README.md",
        "CHANGELOG.md",
        "site/index.html",
        ".github/workflows/validate.yml",
        ".github/workflows/pages.yml",
        "tests/runner/test-runnerctl-contracts.sh",
    ):
        target = temp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source = ROOT / relative
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return temp


def test_bump_math():
    module = load_module()
    assert module.bump_version("0.5.0", "patch") == "0.5.1"
    assert module.bump_version("0.5.9", "minor") == "0.6.0"
    assert module.bump_version("0.9.9", "major") == "1.0.0"


def test_patch_updates_public_identity():
    module = load_module()
    current = module.current_version(ROOT)
    expected = module.bump_version(current, "patch")
    temp = fixture_root()
    try:
        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--root",
                str(temp),
                "--bump",
                "patch",
                "--pr-number",
                "999",
                "--pr-title",
                "feat: test automatic release",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert completed.stdout.strip() == expected
        assert f'RUNNERCTL_VERSION="{expected}"' in (temp / "runnerctl").read_text()
        assert f'EXPECTED_RUNNERCTL_VERSION="{expected}"' in (
            temp / "tests/runner/test-runnerctl-contracts.sh"
        ).read_text()
        assert f"releases/tag/v{expected}" in (temp / "README.md").read_text()
        assert f"RunnerOps v{expected}" in (temp / "site/index.html").read_text()
        assert f"RunnerOps v{expected}" in (
            temp / ".github/workflows/validate.yml"
        ).read_text()
        changelog = (temp / "CHANGELOG.md").read_text()
        assert f"## v{expected}" in changelog
        assert "PR #999" in changelog
    finally:
        shutil.rmtree(temp)


def main():
    test_bump_math()
    test_patch_updates_public_identity()
    print("[PASS] release version automation contracts")


if __name__ == "__main__":
    main()
