#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

"""Prepare one disposable backend tree and frontend source mirror."""

from __future__ import annotations

import argparse
import os
import stat
from contextlib import suppress
from pathlib import Path

RUN_ROOT_PREFIX = ".moldy-test-run."
IGNORED_DIRECTORIES = {
    ".auth",
    ".next",
    "node_modules",
    "output",
    "playwright-artifacts",
    "playwright-report",
    "test-results",
}
IGNORED_FILES = {"next-env.d.ts"}
PREPARED_DIRECTORIES = (
    "backend/data/skills",
    "backend/data/upstreams/k-skill",
    "backend/data/marketplace/k-skill",
    "backend/data/conversations",
    "backend/data/uploads",
    "backend/data/artifacts",
    "backend/data/agents",
    "backend/data/users",
    "frontend/auth/scripted-smoke",
    "frontend/auth/scripted-full",
    "frontend/auth/scripted-capture",
    "frontend/auth/live-manual",
    "frontend/.next/scripted-smoke",
    "frontend/.next/scripted-full",
    "frontend/.next/scripted-capture",
    "frontend/.next/live-manual",
    "frontend/test-results/scripted-smoke",
    "frontend/test-results/scripted-full",
    "frontend/test-results/scripted-capture",
    "frontend/test-results/live-manual",
    "frontend/playwright-artifacts/scripted-smoke",
    "frontend/playwright-artifacts/scripted-full",
    "frontend/playwright-artifacts/live-manual",
    "output/captures",
    "output/e2e-captures",
)


def _open_directory(name: str | Path, *, dir_fd: int | None = None) -> int:
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=dir_fd,
    )


def _ensure_directory(root_fd: int, relative: str) -> None:
    current_fd = os.dup(root_fd)
    try:
        for component in Path(relative).parts:
            with suppress(FileExistsError):
                os.mkdir(component, 0o700, dir_fd=current_fd)
            child_fd = _open_directory(component, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = child_fd
    finally:
        os.close(current_fd)


def _ignored(name: str, *, directory: bool) -> bool:
    if name.startswith(".env") or name.endswith(".tsbuildinfo"):
        return True
    return name in (IGNORED_DIRECTORIES if directory else IGNORED_FILES)


def _copy_file(source: Path, destination_fd: int, name: str) -> None:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("frontend source entry is not a regular file")
        target_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            metadata.st_mode & 0o777,
            dir_fd=destination_fd,
        )
        try:
            while data := os.read(source_fd, 1024 * 1024):
                view = memoryview(data)
                while view:
                    written = os.write(target_fd, view)
                    if written == 0:
                        raise OSError("short mirror write")
                    view = view[written:]
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)


def _copy_tree(source: Path, destination_fd: int) -> None:
    with os.scandir(source) as entries:
        for entry in entries:
            if entry.is_symlink():
                if _ignored(entry.name, directory=entry.is_dir(follow_symlinks=True)):
                    continue
                raise OSError("frontend source tree contains a symbolic link")
            if entry.is_dir(follow_symlinks=False):
                if _ignored(entry.name, directory=True):
                    continue
                os.mkdir(entry.name, 0o700, dir_fd=destination_fd)
                child_fd = _open_directory(entry.name, dir_fd=destination_fd)
                try:
                    _copy_tree(Path(entry.path), child_fd)
                finally:
                    os.close(child_fd)
                continue
            if not _ignored(entry.name, directory=False):
                _copy_file(Path(entry.path), destination_fd, entry.name)


def prepare_run_root(run_root: Path, repo_root: Path) -> None:
    """Populate the exact wrapper-owned root without following destination links."""
    if not run_root.is_absolute() or not run_root.name.startswith(RUN_ROOT_PREFIX):
        raise OSError("run root is not wrapper-owned")
    root_fd = _open_directory(run_root)
    try:
        for relative in PREPARED_DIRECTORIES:
            _ensure_directory(root_fd, relative)
        frontend_fd = _open_directory("frontend", dir_fd=root_fd)
        try:
            _copy_tree(repo_root / "frontend", frontend_fd)
            modules = repo_root / "frontend" / "node_modules"
            if not modules.is_dir():
                raise OSError("frontend dependencies are unavailable")
            os.symlink(modules, "node_modules", target_is_directory=True, dir_fd=frontend_fd)
        finally:
            os.close(frontend_fd)
    finally:
        os.close(root_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root")
    parser.add_argument("repo_root")
    arguments = parser.parse_args()
    try:
        prepare_run_root(Path(arguments.run_root), Path(arguments.repo_root))
    except OSError:
        print("prepare_failed")
        return 2
    print("prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
