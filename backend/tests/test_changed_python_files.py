"""Git-state and path-boundary tests for the changed Python collector."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "scripts"))

from changed_python_files import (  # noqa: E402
    ChangedPythonError,
    _parse_name_status,
    _regular_contained_python,
)

TYPE_CHECKER = BACKEND_ROOT / "scripts" / "check_changed_python_types.py"
FORMAT_CHECKER = BACKEND_ROOT / "scripts" / "check_changed_python_format.py"
CHECKERS = (TYPE_CHECKER, FORMAT_CHECKER)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(
        [executable, *args],
        cwd=repo,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Collector Test",
            "GIT_AUTHOR_EMAIL": "collector@example.invalid",
            "GIT_COMMITTER_NAME": "Collector Test",
            "GIT_COMMITTER_EMAIL": "collector@example.invalid",
        },
        capture_output=True,
        check=check,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.name", "Collector Test")
    _git(repo, "config", "user.email", "collector@example.invalid")
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


def _write(repo: Path, relative: str, content: str = "value = 1\n") -> Path:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


@pytest.mark.parametrize("checker", CHECKERS)
def test_collector_unions_git_states(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given Python paths in committed, staged, unstaged, and untracked states.
    repo, base = git_repo
    _write(repo, "committed.py")
    _git(repo, "add", "--", "committed.py")
    _git(repo, "commit", "-qm", "committed")
    _write(repo, "staged.py")
    _git(repo, "add", "--", "staged.py")
    _write(repo, "unstaged.py")
    _git(repo, "add", "--", "unstaged.py")
    _git(repo, "commit", "-qm", "track unstaged")
    _write(repo, "unstaged.py", "value = 2\n")
    _write(repo, "untracked.pyi", "value: int\n")

    # When a checker collects the final union.
    result = _run(checker, repo, base)

    # Then each path is selected once.
    assert result.returncode == 0
    assert "selected=4" in result.stdout


@pytest.mark.parametrize("checker", CHECKERS)
def test_collector_deduplicates_git_states(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given one path changed in the commit, index, and worktree.
    repo, base = git_repo
    path = _write(repo, "shared.py")
    _git(repo, "add", "--", "shared.py")
    _git(repo, "commit", "-qm", "shared")
    path.write_text("value = 2\n")
    _git(repo, "add", "--", "shared.py")
    path.write_text("value = 3\n")

    # When the checker collects the union.
    result = _run(checker, repo, base)

    # Then the path is selected exactly once.
    assert result.returncode == 0
    assert "selected=1" in result.stdout


@pytest.mark.parametrize("checker", CHECKERS)
def test_collector_uses_final_rename_extension(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given Python-to-text, text-to-Python, and deleted Python paths.
    repo, base = git_repo
    for name in ("old.py", "becomes.py.txt", "deleted.py"):
        _write(repo, name)
    _git(repo, "add", "--", "old.py", "becomes.py.txt", "deleted.py")
    _git(repo, "commit", "-qm", "rename sources")
    _git(repo, "mv", "old.py", "old.txt")
    _git(repo, "mv", "becomes.py.txt", "becomes.py")
    _git(repo, "rm", "-q", "deleted.py")

    # When the checker collects final destinations.
    result = _run(checker, repo, base)

    # Then only the final Python destination is selected.
    assert result.returncode == 0
    assert "selected=1" in result.stdout


@pytest.mark.parametrize("checker", CHECKERS)
@pytest.mark.parametrize(
    "filename", ["space name.py", "tab\tname.py", "line\nname.py", "-leading.py", "한글.py"]
)
def test_collector_handles_nul_safe_names(
    checker: Path, filename: str, git_repo: tuple[Path, str]
) -> None:
    # Given a filename unsafe for line parsing.
    repo, base = git_repo
    _write(repo, filename)

    # When the checker collects it.
    result = _run(checker, repo, base)

    # Then it is selected without being echoed.
    assert result.returncode == 0
    assert "selected=1" in result.stdout
    assert filename not in result.stdout + result.stderr


@pytest.mark.parametrize("checker", CHECKERS)
@pytest.mark.parametrize(
    "base", ["missing", "deadbee", "--help", "../HEAD", "a;printf_SECRET", "123456"]
)
def test_collector_rejects_invalid_base_without_echo(
    checker: Path, base: str, git_repo: tuple[Path, str]
) -> None:
    # Given an invalid, missing, or option-like base.
    repo, _ = git_repo

    # When the checker resolves it.
    result = _run(checker, repo, base)

    # Then it fails without reflecting the value.
    assert result.returncode != 0
    assert base not in result.stdout + result.stderr


@pytest.mark.parametrize("checker", CHECKERS)
def test_collector_rejects_nonancestor(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given a valid commit outside HEAD ancestry.
    repo, _ = git_repo
    _git(repo, "checkout", "-qb", "other")
    _write(repo, "other.txt")
    _git(repo, "add", "--", "other.txt")
    _git(repo, "commit", "-qm", "other")
    nonancestor = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    _git(repo, "checkout", "-q", "main")

    # When a checker resolves it.
    result = _run(checker, repo, nonancestor)

    # Then it fails without echoing the SHA.
    assert result.returncode != 0
    assert nonancestor not in result.stdout + result.stderr


@pytest.mark.parametrize("checker", CHECKERS)
def test_collector_rejects_unmerged_state(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given an unresolved Python merge conflict.
    repo, base = git_repo
    _write(repo, "conflict.py")
    _git(repo, "add", "--", "conflict.py")
    _git(repo, "commit", "-qm", "base conflict")
    _git(repo, "checkout", "-qb", "topic")
    _write(repo, "conflict.py", "value = 2\n")
    _git(repo, "commit", "-qam", "topic")
    _git(repo, "checkout", "-q", "main")
    _write(repo, "conflict.py", "value = 3\n")
    _git(repo, "commit", "-qam", "main")
    _git(repo, "merge", "topic", check=False)

    # When a checker collects the ambiguous state.
    result = _run(checker, repo, base)

    # Then collection fails closed.
    assert result.returncode != 0
    assert "selected=" not in result.stdout


@pytest.mark.parametrize("checker", CHECKERS)
def test_collector_rejects_final_symlink(checker: Path, git_repo: tuple[Path, str]) -> None:
    # Given an untracked Python symlink.
    repo, base = git_repo
    outside = repo.parent / "outside.py"
    outside.write_text("value = 1\n")
    (repo / "linked.py").symlink_to(outside)

    # When the checker collects it.
    result = _run(checker, repo, base)

    # Then it fails without exposing the target.
    assert result.returncode != 0
    assert str(outside) not in result.stdout + result.stderr


def test_collector_rejects_symlink_component(git_repo: tuple[Path, str]) -> None:
    # Given a candidate below a symlinked directory.
    repo, _ = git_repo
    outside = repo.parent / "outside"
    outside.mkdir()
    (outside / "nested.py").write_text("value = 1\n")
    (repo / "alias").symlink_to(outside, target_is_directory=True)

    # When the candidate is validated.
    with pytest.raises(ChangedPythonError):
        _regular_contained_python(repo, "alias/nested.py")

    # Then the repository remains inspectable.
    assert _git(repo, "status", "--porcelain=v1", "-z").returncode == 0


@pytest.mark.parametrize(
    "stream",
    [
        b"M\0/absolute.py\0",
        b"M\0./dot.py\0",
        b"M\0../escape.py\0",
        b"M\0missing-terminator.py",
        b"U\0unmerged.py\0",
        b"R100\0only-old.py\0",
        b"R\0old.py\0new.py\0",
        b"C\0old.py\0new.py\0",
        b"Mgarbage\0changed.py\0",
        b"A1\0added.py\0",
        b"D1\0deleted.py\0",
        b"T100\0changed.py\0",
        b"Rfoo\0old.py\0new.py\0",
        b"R-1\0old.py\0new.py\0",
        b"R101\0old.py\0new.py\0",
        b"C999\0old.py\0new.py\0",
        b"C0000\0old.py\0new.py\0",
        b"R100\0old.py\0new.py\0extra.py\0",
    ],
)
def test_collector_rejects_malformed_name_status(stream: bytes) -> None:
    # Given malformed grammar, path, or record arity.

    # When the NUL parser consumes it.
    with pytest.raises(ChangedPythonError):
        _parse_name_status(stream)

    # Then parsing has failed before filesystem access.


@pytest.mark.parametrize("status", [b"R0", b"R100", b"C0", b"C75", b"C100"])
def test_collector_uses_valid_rename_and_copy_destinations(status: bytes) -> None:
    # Given a valid scored rename/copy record emitted by Git.
    stream = status + b"\0old.py\0destination.py\0"

    # When the stream is parsed.
    records = _parse_name_status(stream)

    # Then only the final destination is returned.
    assert records == [(chr(status[0]), "destination.py")]
