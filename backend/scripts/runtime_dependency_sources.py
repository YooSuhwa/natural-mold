"""Safely load the tracked Python sources used by the dependency guard."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

MAX_SOURCE_BYTES: Final = 1_048_576
GIT_TIMEOUT_SECONDS: Final = 30
CHECKED_PREFIXES: Final = (
    "app.routers",
    "app.services",
    "app.models",
    "app.agent_runtime",
)


@dataclass(frozen=True, slots=True)
class DependencyGraphError(Exception):
    """A fail-closed source inventory or parsing failure."""

    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True, order=True)
class ModuleSource:
    """One checked Python module and its repository-relative source path."""

    module: str
    path: str
    source: str


def _module_name(relative: PurePosixPath) -> str:
    without_suffix = relative.with_suffix("")
    parts = without_suffix.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_checked(module: str) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in CHECKED_PREFIXES)


def _git_tracked_python(backend_root: Path) -> tuple[str, ...]:
    executable = shutil.which("git")
    if executable is None:
        raise DependencyGraphError("git executable is unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable, no shell
            [executable, "ls-files", "-z", "--", "app"],
            cwd=backend_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DependencyGraphError("git source inventory failed") from error
    if result.returncode != 0 or (result.stdout and not result.stdout.endswith(b"\0")):
        raise DependencyGraphError("git source inventory is malformed")
    paths: list[str] = []
    records = result.stdout[:-1].split(b"\0") if result.stdout else []
    for raw in records:
        try:
            relative = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DependencyGraphError("tracked source path is not UTF-8") from error
        pure = PurePosixPath(relative)
        if (
            not relative
            or pure.is_absolute()
            or pure.suffix != ".py"
            or pure.parts[:1] != ("app",)
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            continue
        if _is_checked(_module_name(pure)):
            paths.append(relative)
    return tuple(sorted(set(paths)))


def _read_regular_source(backend_root: Path, relative: str) -> ModuleSource:
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or pure.suffix != ".py"
        or pure.parts[:1] != ("app",)
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in relative
    ):
        raise DependencyGraphError(f"tracked source path is unsafe: {relative}")
    current = backend_root
    for part in pure.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise DependencyGraphError(f"tracked source is unreadable: {relative}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise DependencyGraphError(f"tracked source traverses a symbolic link: {relative}")
    if not stat.S_ISREG(metadata.st_mode):
        raise DependencyGraphError(f"tracked source is not a regular file: {relative}")
    root = backend_root.resolve()
    if os.path.commonpath((root, current.resolve())) != str(root):
        raise DependencyGraphError(f"tracked source escapes backend root: {relative}")
    if metadata.st_size > MAX_SOURCE_BYTES:
        raise DependencyGraphError(f"tracked source exceeds size limit: {relative}")
    try:
        source = current.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DependencyGraphError(f"tracked source is not readable UTF-8: {relative}") from error
    module = _module_name(pure)
    if not _is_checked(module):
        raise DependencyGraphError(f"tracked source is outside guarded packages: {relative}")
    return ModuleSource(module, relative, source)


def load_selected_sources(
    backend_root: Path, tracked_files: Sequence[str]
) -> tuple[ModuleSource, ...]:
    """Load an explicit, deterministic source inventory for test probes."""
    root = backend_root.resolve()
    return tuple(_read_regular_source(root, path) for path in sorted(set(tracked_files)))


def load_tracked_sources(backend_root: Path) -> tuple[ModuleSource, ...]:
    """Load guarded, Git-tracked sources without following symbolic links."""
    root = backend_root.resolve()
    return load_selected_sources(root, _git_tracked_python(root))
