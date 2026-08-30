"""Executable GitHub-event base resolution tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RESOLVER = BACKEND_ROOT / "scripts" / "resolve_quality_base.py"
TYPE_CHECKER = BACKEND_ROOT / "scripts" / "check_changed_python_types.py"
FORMAT_CHECKER = BACKEND_ROOT / "scripts" / "check_changed_python_format.py"
QUALITY_ENV = {
    "QUALITY_EVENT_NAME",
    "QUALITY_PR_BASE_SHA",
    "QUALITY_PUSH_BEFORE_SHA",
    "QUALITY_DEFAULT_BRANCH",
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(
        [executable, *args],
        cwd=repo,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Resolver Test",
            "GIT_AUTHOR_EMAIL": "resolver@example.invalid",
            "GIT_COMMITTER_NAME": "Resolver Test",
            "GIT_COMMITTER_EMAIL": "resolver@example.invalid",
        },
        capture_output=True,
        check=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.name", "Resolver Test")
    _git(repo, "config", "user.email", "resolver@example.invalid")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "--", "README.md")
    _git(repo, "commit", "-qm", "initial")
    return repo, _git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def _run(repo: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    inherited = {key: value for key, value in os.environ.items() if key not in QUALITY_ENV}
    return subprocess.run(
        [sys.executable, str(RESOLVER)],
        cwd=repo,
        env={**inherited, **environment},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _check(checker: Path, repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(checker), "--base", base],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    ("environment_key", "event_name"),
    [("QUALITY_PR_BASE_SHA", "pull_request"), ("QUALITY_PUSH_BEFORE_SHA", "push")],
)
def test_resolver_uses_explicit_event_base(
    environment_key: str, event_name: str, git_repo: tuple[Path, str]
) -> None:
    # Given a pull request or normal push with an explicit base.
    repo, base = git_repo

    # When the resolver runs.
    result = _run(repo, {"QUALITY_EVENT_NAME": event_name, environment_key: base})

    # Then it emits only that full SHA.
    assert result.returncode == 0
    assert result.stdout == f"{base}\n"
    assert result.stderr == ""


def test_resolver_uses_default_merge_base_for_new_branch(git_repo: tuple[Path, str]) -> None:
    # Given a new branch two Python commits ahead of the remote default branch.
    repo, base = git_repo
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    for index in range(2):
        path = repo / f"commit_{index}.py"
        path.write_text(f"value_{index} = {index}\n")
        _git(repo, "add", "--", path.name)
        _git(repo, "commit", "-qm", f"commit {index}")

    # When a zero-before push is resolved and checked.
    result = _run(
        repo,
        {
            "QUALITY_EVENT_NAME": "push",
            "QUALITY_PUSH_BEFORE_SHA": "0" * 40,
            "QUALITY_DEFAULT_BRANCH": "main",
        },
    )
    checked = _check(FORMAT_CHECKER, repo, result.stdout.strip())

    # Then merge-base selects both branch changes rather than only HEAD's parent.
    assert result.returncode == 0
    assert result.stdout == f"{base}\n"
    assert result.stdout.strip() != _git(repo, "rev-parse", "HEAD^").stdout.decode().strip()
    assert checked.returncode == 0
    assert "selected=2" in checked.stdout


@pytest.mark.parametrize("default_branch", ["-invalid", "missing"])
def test_resolver_rejects_invalid_or_missing_default_ref(
    default_branch: str, git_repo: tuple[Path, str]
) -> None:
    # Given an invalid or unavailable remote default ref.
    repo, _ = git_repo

    # When the zero-before fallback runs.
    result = _run(
        repo,
        {
            "QUALITY_EVENT_NAME": "push",
            "QUALITY_PUSH_BEFORE_SHA": "0" * 40,
            "QUALITY_DEFAULT_BRANCH": default_branch,
        },
    )

    # Then it fails without reflecting the branch value.
    assert result.returncode != 0
    assert default_branch not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "environment",
    [
        {"QUALITY_EVENT_NAME": "workflow_dispatch"},
        {"QUALITY_EVENT_NAME": "pull_request", "QUALITY_PR_BASE_SHA": "a" * 40 + ";cmd"},
        {
            "QUALITY_EVENT_NAME": "push",
            "QUALITY_PUSH_BEFORE_SHA": "0" * 40,
            "QUALITY_DEFAULT_BRANCH": "main;cmd",
        },
    ],
)
def test_resolver_rejects_malformed_event_values(
    environment: dict[str, str], git_repo: tuple[Path, str]
) -> None:
    # Given malformed event metadata.
    repo, _ = git_repo

    # When the resolver parses the boundary.
    result = _run(repo, environment)

    # Then fixed output reflects none of the supplied values.
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "quality base resolution failed\n"


def test_nonancestor_before_is_not_fallback_and_checker_rejects(
    git_repo: tuple[Path, str],
) -> None:
    # Given a nonzero before commit outside current HEAD ancestry.
    repo, _ = git_repo
    _git(repo, "checkout", "-qb", "other")
    (repo / "other.py").write_text("other = 1\n")
    _git(repo, "add", "--", "other.py")
    _git(repo, "commit", "-qm", "other")
    nonancestor = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    _git(repo, "checkout", "-q", "main")

    # When the resolver emits before and the checker validates ancestry.
    resolved = _run(
        repo,
        {"QUALITY_EVENT_NAME": "push", "QUALITY_PUSH_BEFORE_SHA": nonancestor},
    )
    checked = _check(TYPE_CHECKER, repo, resolved.stdout.strip())

    # Then no fallback occurs and the checker fails closed.
    assert resolved.returncode == 0
    assert resolved.stdout == f"{nonancestor}\n"
    assert checked.returncode != 0
