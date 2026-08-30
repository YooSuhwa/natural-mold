#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

# ─── How to run ───
# 1. Export the QUALITY_EVENT_NAME and event-specific QUALITY_* variables.
# 2. Run from the repository: uv run python backend/scripts/resolve_quality_base.py
# ──────────────────

"""Resolve one explicit full commit SHA for changed-file CI ratchets."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

FULL_SHA: Final = re.compile(r"[0-9a-fA-F]{40}\Z")
BRANCH_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
ZERO_SHA: Final = "0" * 40
GIT_TIMEOUT_SECONDS: Final = 30


@dataclass(frozen=True, slots=True)
class QualityBaseError(Exception):
    """A bounded base-resolution failure safe for the CLI boundary."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def _full_sha(value: str) -> str:
    if FULL_SHA.fullmatch(value) is None:
        raise QualityBaseError("commit id is invalid")
    return value.lower()


def _default_branch(value: str) -> str:
    if (
        BRANCH_NAME.fullmatch(value) is None
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
    ):
        raise QualityBaseError("default branch is invalid")
    return value


def _git(repo: Path, arguments: list[str]) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise QualityBaseError("git executable is unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable; argv only
            [executable, *arguments],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise QualityBaseError("git invocation failed") from error
    if result.returncode != 0:
        raise QualityBaseError("required git history is unavailable")
    return result.stdout


def _new_branch_base(repo: Path, default_branch: str) -> str:
    branch = _default_branch(default_branch)
    remote_ref = f"refs/remotes/origin/{branch}"
    remote_raw = _git(repo, ["rev-parse", "--verify", f"{remote_ref}^{{commit}}", "--"])
    try:
        remote_sha = _full_sha(remote_raw.decode("ascii").strip())
    except UnicodeDecodeError as error:
        raise QualityBaseError("remote commit is invalid") from error
    base_raw = _git(repo, ["merge-base", "HEAD", remote_sha])
    try:
        base = _full_sha(base_raw.decode("ascii").strip())
    except UnicodeDecodeError as error:
        raise QualityBaseError("merge base is invalid") from error
    _git(repo, ["merge-base", "--is-ancestor", base, "HEAD"])
    return base


def resolve_quality_base(
    event_name: str,
    pull_request_base: str,
    push_before: str,
    default_branch: str,
    *,
    repo: Path,
) -> str:
    """Resolve the event base without falling back from invalid supplied commits."""
    if event_name == "pull_request":
        return _full_sha(pull_request_base)
    if event_name != "push":
        raise QualityBaseError("event is unsupported")
    before = _full_sha(push_before)
    if before != ZERO_SHA:
        return before
    return _new_branch_base(repo, default_branch)


def main() -> int:
    """Read GitHub event values from the environment and emit only a validated SHA."""
    try:
        base = resolve_quality_base(
            os.environ.get("QUALITY_EVENT_NAME", ""),
            os.environ.get("QUALITY_PR_BASE_SHA", ""),
            os.environ.get("QUALITY_PUSH_BEFORE_SHA", ""),
            os.environ.get("QUALITY_DEFAULT_BRANCH", ""),
            repo=Path.cwd(),
        )
    except QualityBaseError:
        print("quality base resolution failed", file=sys.stderr)
        return 2
    print(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
