#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path
from typing import Optional, Tuple

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str) -> Tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())


def format_version(parts: Tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in parts)


def bump_version(current: str, bump: str) -> str:
    major, minor, patch = parse_version(current)
    if bump == "patch":
        patch += 1
    elif bump == "minor":
        minor += 1
        patch = 0
    elif bump == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ValueError(f"unsupported bump: {bump}")
    return format_version((major, minor, patch))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def replace_exact(content: str, old: str, new: str, *, path: Path, count: int = 1) -> str:
    actual = content.count(old)
    if actual != count:
        raise RuntimeError(
            f"{path}: expected {count} occurrence(s) of {old!r}, found {actual}"
        )
    return content.replace(old, new)


def current_version(root: Path) -> str:
    content = read_text(root / "runnerctl")
    match = re.search(r'^RUNNERCTL_VERSION="([^"]+)"$', content, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("runnerctl: RUNNERCTL_VERSION not found")
    parse_version(match.group(1))
    return match.group(1)


def changelog_entry(version: str, *, pr_number: Optional[str], pr_title: Optional[str], manual: bool, bump: str) -> str:
    date = dt.date.today().isoformat()
    if manual:
        detail = f"Manual {bump} release requested through the release workflow."
    elif pr_number and pr_title:
        clean_title = " ".join(pr_title.split()).replace(chr(96), "'")
        detail = f"Automated release after PR #{pr_number} — {clean_title}."
    elif pr_number:
        detail = f"Automated release after PR #{pr_number}."
    else:
        detail = "Automated release after a merged pull request."
    return (
        f"## v{version} — {date}\n\n"
        "### Changed\n\n"
        f"- {detail}\n\n"
    )


def update_files(
    root: Path,
    *,
    bump: str,
    pr_number: Optional[str] = None,
    pr_title: Optional[str] = None,
    manual: bool = False,
) -> Tuple[str, str]:
    old = current_version(root)
    new = bump_version(old, bump)

    runnerctl = root / "runnerctl"
    content = read_text(runnerctl)
    content = replace_exact(
        content,
        f'RUNNERCTL_VERSION="{old}"',
        f'RUNNERCTL_VERSION="{new}"',
        path=runnerctl,
    )
    write_text(runnerctl, content)

    version_test = root / "tests/runner/test-runnerctl-contracts.sh"
    content = read_text(version_test)
    content = replace_exact(
        content,
        f'EXPECTED_RUNNERCTL_VERSION="{old}"',
        f'EXPECTED_RUNNERCTL_VERSION="{new}"',
        path=version_test,
    )
    write_text(version_test, content)

    readme = root / "README.md"
    content = read_text(readme)
    old_release = (
        f"**Release estável atual:** [v{old}]"
        f"(https://github.com/oalangomes/RunnerOps/releases/tag/v{old})"
    )
    new_release = (
        f"**Release estável atual:** [v{new}]"
        f"(https://github.com/oalangomes/RunnerOps/releases/tag/v{new})"
    )
    content = replace_exact(content, old_release, new_release, path=readme)
    write_text(readme, content)

    site = root / "site/index.html"
    content = read_text(site)
    url_old = f"https://github.com/oalangomes/RunnerOps/releases/tag/v{old}"
    url_new = f"https://github.com/oalangomes/RunnerOps/releases/tag/v{new}"
    if content.count(url_old) < 1:
        raise RuntimeError(f"{site}: release URL for v{old} not found")
    content = content.replace(url_old, url_new)
    identity_old = f"RunnerOps v{old}"
    identity_new = f"RunnerOps v{new}"
    if content.count(identity_old) < 1:
        raise RuntimeError(f"{site}: release identity for v{old} not found")
    content = content.replace(identity_old, identity_new)
    write_text(site, content)

    for relative in (
        ".github/workflows/validate.yml",
        ".github/workflows/pages.yml",
    ):
        path = root / relative
        content = read_text(path)
        content = replace_exact(
            content,
            f"RunnerOps v{old}",
            f"RunnerOps v{new}",
            path=path,
        )
        write_text(path, content)

    changelog = root / "CHANGELOG.md"
    content = read_text(changelog)
    marker = "## Unreleased\n\n"
    if content.count(marker) != 1:
        raise RuntimeError(f"{changelog}: expected one Unreleased marker")
    if f"## v{new} " in content:
        raise RuntimeError(f"{changelog}: v{new} already exists")
    content = content.replace(
        marker,
        marker
        + changelog_entry(
            new,
            pr_number=pr_number,
            pr_title=pr_title,
            manual=manual,
            bump=bump,
        ),
        1,
    )
    write_text(changelog, content)

    return old, new


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Advance RunnerOps release identity and changelog."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--bump", required=True, choices=("patch", "minor", "major"))
    parser.add_argument("--pr-number")
    parser.add_argument("--pr-title")
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    _, new = update_files(
        root,
        bump=args.bump,
        pr_number=args.pr_number,
        pr_title=args.pr_title,
        manual=args.manual,
    )
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
