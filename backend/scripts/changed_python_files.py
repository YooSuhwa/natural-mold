"""Collect final regular Python paths changed since an explicit Git base."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

BASE_PATTERN: Final = re.compile(r"[0-9a-fA-F]{7,40}\Z")
PYTHON_SUFFIXES: Final = frozenset({".py", ".pyi"})
GIT_TIMEOUT_SECONDS: Final = 30


@dataclass(frozen=True, slots=True)
class ChangedPythonError(Exception):
    """A bounded collector failure safe to report at a CLI boundary."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def _git(repo: Path, arguments: list[str]) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise ChangedPythonError("git executable is unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable; argv only, no shell
            [executable, *arguments],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ChangedPythonError("git invocation failed") from error
    if result.returncode != 0:
        raise ChangedPythonError("git rejected repository state")
    return result.stdout


def _repository_root() -> Path:
    raw = _git(Path.cwd(), ["rev-parse", "--show-toplevel"])
    try:
        decoded = raw.decode().strip()
    except UnicodeDecodeError as error:
        raise ChangedPythonError("repository path is not decodable") from error
    root = Path(decoded)
    if not root.is_absolute() or not root.is_dir():
        raise ChangedPythonError("repository root is invalid")
    return root.resolve()


def _resolve_base(repo: Path, base: str) -> str:
    if BASE_PATTERN.fullmatch(base) is None:
        raise ChangedPythonError("base must be a hexadecimal commit id")
    raw = _git(repo, ["rev-parse", "--verify", f"{base}^{{commit}}", "--"])
    try:
        full = raw.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ChangedPythonError("resolved commit is invalid") from error
    if re.fullmatch(r"[0-9a-f]{40}", full) is None:
        raise ChangedPythonError("resolved commit is invalid")
    _git(repo, ["merge-base", "--is-ancestor", full, "HEAD", "--"])
    return full


def _decode_path(raw: bytes) -> str:
    try:
        value = os.fsdecode(raw)
    except UnicodeError as error:
        raise ChangedPythonError("changed path is not decodable") from error
    if not value or "\x00" in value or value.startswith("/"):
        raise ChangedPythonError("changed path is invalid")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ChangedPythonError("changed path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ChangedPythonError("changed path is invalid")
    return value


def _parse_name_status(raw: bytes) -> list[tuple[str, str]]:
    records = raw.split(b"\0")
    if not records or records[-1] != b"":
        raise ChangedPythonError("git change stream is malformed")
    records.pop()
    parsed: list[tuple[str, str]] = []
    index = 0
    while index < len(records):
        status_raw = records[index]
        index += 1
        try:
            status_text = status_raw.decode("ascii")
        except UnicodeDecodeError as error:
            raise ChangedPythonError("git status is malformed") from error
        kind = status_text[:1]
        if kind in {"A", "D", "M", "T"}:
            if status_text != kind:
                raise ChangedPythonError("git status is malformed")
        elif kind in {"R", "C"}:
            score = status_text[1:]
            if not score or len(score) > 3 or not score.isdecimal() or int(score) > 100:
                raise ChangedPythonError("git status score is malformed")
        else:
            raise ChangedPythonError("git status is unsupported")
        path_count = 2 if kind in {"R", "C"} else 1
        if index + path_count > len(records):
            raise ChangedPythonError("git change stream is malformed")
        paths = [_decode_path(item) for item in records[index : index + path_count]]
        index += path_count
        parsed.append((kind, paths[-1]))
    return parsed


def _parse_untracked(raw: bytes) -> list[str]:
    records = raw.split(b"\0")
    if not records or records[-1] != b"":
        raise ChangedPythonError("git untracked stream is malformed")
    return [_decode_path(item) for item in records[:-1]]


def _regular_contained_python(repo: Path, relative: str) -> Path | None:
    pure = PurePosixPath(relative)
    if pure.suffix not in PYTHON_SUFFIXES:
        return None
    current = repo
    for part in pure.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode):
            raise ChangedPythonError("changed path traverses a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise ChangedPythonError("changed Python path is not a regular file")
    if os.path.commonpath((repo, current)) != str(repo):
        raise ChangedPythonError("changed path escapes repository")
    return current


def collect_changed_python(base: str) -> tuple[Path, ...]:
    """Return a deterministic union of final regular Python paths since base."""
    repo = _repository_root()
    full_base = _resolve_base(repo, base)
    diff_commands = (
        [
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--find-copies",
            f"{full_base}..HEAD",
            "--",
        ],
        ["diff", "--name-status", "-z", "--find-renames", "--find-copies", "--cached", "--"],
        ["diff", "--name-status", "-z", "--find-renames", "--find-copies", "--"],
    )
    candidates: set[str] = set()
    for command in diff_commands:
        for kind, relative in _parse_name_status(_git(repo, command)):
            if kind != "D":
                candidates.add(relative)
    candidates.update(
        _parse_untracked(_git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--"]))
    )
    selected = [
        path
        for relative in sorted(candidates, key=os.fsencode)
        if (path := _regular_contained_python(repo, relative)) is not None
    ]
    return tuple(selected)
