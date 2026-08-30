"""Checker process and tool-boundary tests for changed Python quality."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT / "scripts"))

import check_changed_python_format as format_checker_module  # noqa: E402
import check_changed_python_types as type_checker_module  # noqa: E402

TYPE_CHECKER = BACKEND_ROOT / "scripts" / "check_changed_python_types.py"
FORMAT_CHECKER = BACKEND_ROOT / "scripts" / "check_changed_python_format.py"
CHECKERS = (TYPE_CHECKER, FORMAT_CHECKER)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(
        [executable, *args],
        cwd=repo,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Quality Test",
            "GIT_AUTHOR_EMAIL": "quality@example.invalid",
            "GIT_COMMITTER_NAME": "Quality Test",
            "GIT_COMMITTER_EMAIL": "quality@example.invalid",
        },
        capture_output=True,
        check=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.name", "Quality Test")
    _git(repo, "config", "user.email", "quality@example.invalid")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "--", "README.md")
    _git(repo, "commit", "-qm", "initial")
    return repo, _git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def _run(checker: Path, repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(checker), "--base", base],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("checker", CHECKERS)
def test_checker_succeeds_without_changed_python(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given a repository whose only change is not Python.
    repo, base = git_repo
    (repo / "notes.txt").write_text("changed\n")

    # When the checker runs.
    result = _run(checker, repo, base)

    # Then it exits without invoking its quality tool.
    assert result.returncode == 0
    assert "selected=0" in result.stdout


@pytest.mark.parametrize(
    ("runner", "expected_prefix"),
    [
        (type_checker_module._run_pyright, "changed-python-types"),
        (format_checker_module._run_ruff, "changed-python-format"),
    ],
)
def test_zero_selection_does_not_invoke_tool(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runner: Callable[[tuple[Path, ...]], int],
    expected_prefix: str,
) -> None:
    # Given a subprocess seam that fails if called.
    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        pytest.fail("quality tool was invoked for an empty selection")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    # When the runner receives no paths.
    result = runner(())

    # Then no subprocess call occurs.
    assert result == 0
    assert capsys.readouterr().out.startswith(expected_prefix)


@pytest.mark.parametrize(
    "runner", [type_checker_module._run_pyright, format_checker_module._run_ruff]
)
@pytest.mark.parametrize("failure", ["timeout", "start"])
def test_tool_failure_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runner: Callable[[tuple[Path, ...]], int],
    failure: str,
    tmp_path: Path,
) -> None:
    # Given a quality tool that times out or cannot start.
    def fail(*_args: object, **_kwargs: object) -> None:
        if failure == "timeout":
            raise subprocess.TimeoutExpired("DUMMY_SECRET", 1)
        raise OSError

    monkeypatch.setattr(subprocess, "run", fail)

    # When the checker invokes the tool.
    result = runner((tmp_path / "Bearer_PRIVATE_KEY.py",))

    # Then it returns one bounded error without reflecting inputs.
    output = capsys.readouterr().out
    assert result == 2
    assert "tool-error" in output
    assert "DUMMY_SECRET" not in output
    assert "Bearer" not in output
    assert str(tmp_path) not in output


def test_type_checker_rejects_real_type_error(git_repo: tuple[Path, str]) -> None:
    # Given an untracked file with an incompatible assignment.
    repo, base = git_repo
    (repo / "broken.py").write_text('value: int = "not an int"\n')

    # When the real Pyright checker runs.
    result = _run(TYPE_CHECKER, repo, base)

    # Then bounded diagnostic counts fail the gate.
    assert result.returncode != 0
    assert "errors=" in result.stdout
    assert "not an int" not in result.stdout + result.stderr


def test_format_checker_rejects_real_format_error(git_repo: tuple[Path, str]) -> None:
    # Given an untracked file requiring Ruff formatting.
    repo, base = git_repo
    (repo / "broken.py").write_text("value    =    1\n")

    # When the real Ruff checker runs.
    result = _run(FORMAT_CHECKER, repo, base)

    # Then it fails without printing source text.
    assert result.returncode != 0
    assert "status=failed" in result.stdout
    assert "value" not in result.stdout + result.stderr


@pytest.mark.parametrize("checker", CHECKERS)
def test_checker_redacts_sensitive_filename_and_temp_path(
    checker: Path, git_repo: tuple[Path, str]
) -> None:
    # Given a malformed file whose name and content resemble sensitive material.
    repo, base = git_repo
    name = "Bearer_DUMMY_SECRET_PRIVATE_KEY.py"
    (repo / name).write_text('"-----BEGIN PRIVATE KEY-----\n')

    # When the checker fails.
    result = _run(checker, repo, base)

    # Then neither filename, content, nor absolute temp path is emitted.
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert all(marker not in output for marker in ("DUMMY_SECRET", "Bearer", "PRIVATE_KEY"))
    assert "BEGIN PRIVATE KEY" not in output
    assert str(repo.parent) not in output


def test_checker_mains_delegate_shared_collector_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given both entrypoints wired to one observable collector and distinct runners.
    targets = (Path("first.py"), Path("second.pyi"))
    collected_bases: list[str] = []
    runner_targets: list[tuple[Path, ...]] = []

    def shared_collect(base: str) -> tuple[Path, ...]:
        collected_bases.append(base)
        return targets

    def type_runner(paths: tuple[Path, ...]) -> int:
        runner_targets.append(paths)
        return 3

    def format_runner(paths: tuple[Path, ...]) -> int:
        runner_targets.append(paths)
        return 4

    monkeypatch.setattr(type_checker_module, "collect_changed_python", shared_collect)
    monkeypatch.setattr(format_checker_module, "collect_changed_python", shared_collect)
    monkeypatch.setattr(type_checker_module, "_run_pyright", type_runner)
    monkeypatch.setattr(format_checker_module, "_run_ruff", format_runner)
    monkeypatch.setattr(sys, "argv", ["checker", "--base", "abcdef0"])

    # When both public main boundaries run.
    return_codes = [type_checker_module.main(), format_checker_module.main()]

    # Then both pass the same base and target tuple to their runner and preserve its result.
    assert collected_bases == ["abcdef0", "abcdef0"]
    assert runner_targets == [targets, targets]
    assert return_codes == [3, 4]


def test_quality_checks_do_not_dirty_real_worktree(git_repo: tuple[Path, str]) -> None:
    # Given the real worktree status digest and a temporary Python change.
    repo, base = git_repo
    before = hashlib.sha256(_git(REPO_ROOT, "status", "--porcelain=v1", "-z").stdout).digest()
    (repo / "safe.py").write_text("safe = 1\n")

    # When both checkers run only in the temporary repository.
    results = [_run(checker, repo, base) for checker in CHECKERS]

    # Then the real worktree remains byte-identical.
    after = hashlib.sha256(_git(REPO_ROOT, "status", "--porcelain=v1", "-z").stdout).digest()
    assert all(result.returncode == 0 for result in results)
    assert after == before
