#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

"""Create and finalize one identity-bound isolated-run manifest."""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
from pathlib import Path

from final_attempt_authority import (
    FinalAttemptAuthorityError,
    verify_open_final_attempt_authority,
)

type ParentIdentity = tuple[int, int]
type FileIdentity = tuple[int, int, int]

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = REPO_ROOT / ".omo/evidence/project-restart-consolidated-roadmap"


def _validate_path_scope(path: Path) -> None:
    absolute = path.absolute()
    if absolute.parent == EVIDENCE_ROOT:
        return
    if absolute.parent.parent != EVIDENCE_ROOT / "final-attempts":
        return
    matched = re.fullmatch(
        r"f2-static\.([a-z][a-z0-9-]{0,63})\.([0-9a-f]{16})\.json",
        absolute.name,
    )
    attempt_id = os.environ.get("MOLDY_FINAL_ATTEMPT_ID")
    node_id = os.environ.get("MOLDY_FINAL_NODE_ID")
    token = os.environ.get("MOLDY_FINAL_RECEIPT_TOKEN")
    head = os.environ.get("MOLDY_FINAL_ATTEMPT_HEAD")
    if (
        matched is None
        or attempt_id is None
        or absolute.parent != EVIDENCE_ROOT / "final-attempts" / attempt_id
        or (matched.group(1), matched.group(2)) != (node_id, token)
        or re.fullmatch(r"[0-9a-f]{64}", attempt_id) is None
        or head is None
        or re.fullmatch(r"[0-9a-f]{40}", head) is None
    ):
        raise OSError("manifest path is outside the producer boundary")
    root_fd = os.open(
        EVIDENCE_ROOT,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        try:
            verify_open_final_attempt_authority(
                evidence_root=EVIDENCE_ROOT,
                evidence_root_fd=root_fd,
                expected_attempt_id=attempt_id,
                expected_attempt_path=absolute.parent,
                expected_head=head,
            )
        except FinalAttemptAuthorityError as error:
            raise OSError("final attempt binding is stale") from error
    finally:
        os.close(root_fd)
    current = subprocess.run(  # noqa: S603 - fixed read-only Git query
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if current.returncode != 0 or current.stdout.strip() != head:
        raise OSError("final attempt binding is stale")


def _identity(metadata: os.stat_result) -> ParentIdentity:
    return metadata.st_dev, metadata.st_ino


def _file_identity(metadata: os.stat_result) -> FileIdentity:
    """Bind a file generation as well as its filesystem object identity."""
    return metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns


def _open_parent(path: Path) -> int:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise OSError("manifest path must be absolute")
    _validate_path_scope(path)
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parent.parts[1:]:
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = child_fd
        metadata = os.fstat(current_fd)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise OSError("manifest parent is not private")
        return current_fd
    except OSError:
        os.close(current_fd)
        raise


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written == 0:
            raise OSError("manifest write made no progress")
        view = view[written:]


def _revalidate_open_manifest(
    parent_fd: int, name: str, file_fd: int, file_identity: FileIdentity
) -> None:
    """Require the visible manifest name to still designate the opened private file."""
    opened = os.fstat(file_fd)
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or opened.st_mode & 0o022
        or _file_identity(opened) != file_identity
        or not stat.S_ISREG(named.st_mode)
        or named.st_mode != opened.st_mode
        or named.st_uid != os.geteuid()
        or named.st_uid != opened.st_uid
        or named.st_nlink != 1
        or named.st_nlink != opened.st_nlink
        or named.st_mode & 0o022
        or _file_identity(named) != _file_identity(opened)
    ):
        raise OSError("manifest identity changed")


def prepare_manifest(path: Path) -> tuple[ParentIdentity, FileIdentity]:
    """Create one private regular file and return parent/file identities."""
    parent_fd = _open_parent(path)
    try:
        file_fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o022
            ):
                raise OSError("manifest is not a private regular file")
            file_identity = _file_identity(metadata)
            os.fsync(file_fd)
            _revalidate_open_manifest(parent_fd, path.name, file_fd, file_identity)
            os.fsync(parent_fd)
            _revalidate_open_manifest(parent_fd, path.name, file_fd, file_identity)
            return _identity(os.fstat(parent_fd)), file_identity
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def verify_manifest(
    path: Path, parent_identity: ParentIdentity, file_identity: FileIdentity
) -> None:
    """Revalidate the final-attempt binding and reserved receipt immediately pre-spawn."""
    parent_fd = _open_parent(path)
    try:
        if _identity(os.fstat(parent_fd)) != parent_identity:
            raise OSError("manifest parent identity changed")
        named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_uid != os.geteuid()
            or named.st_nlink != 1
            or named.st_mode & 0o022
            or _file_identity(named) != file_identity
        ):
            raise OSError("manifest identity changed")
    finally:
        os.close(parent_fd)


def finalize_manifest(
    path: Path, parent_identity: ParentIdentity, file_identity: FileIdentity, payload: bytes
) -> None:
    """Replace bytes only through the still-bound parent and regular file identity."""
    parent_fd = _open_parent(path)
    try:
        if _identity(os.fstat(parent_fd)) != parent_identity:
            raise OSError("manifest parent identity changed")
        file_fd = os.open(
            path.name,
            os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            _revalidate_open_manifest(parent_fd, path.name, file_fd, file_identity)
            os.ftruncate(file_fd, 0)
            _write_all(file_fd, payload)
            os.fsync(file_fd)
            written_file_identity = _file_identity(os.fstat(file_fd))
            _revalidate_open_manifest(parent_fd, path.name, file_fd, written_file_identity)
            _validate_path_scope(path)
            os.fsync(parent_fd)
            _revalidate_open_manifest(parent_fd, path.name, file_fd, written_file_identity)
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def _serialize_identity(identity: ParentIdentity | FileIdentity) -> str:
    return ":".join(str(part) for part in identity)


def _parse_identity(value: str, parts: int) -> tuple[int, ...]:
    fields = value.split(":")
    if len(fields) != parts or any(re.fullmatch(r"[0-9]+", field) is None for field in fields):
        raise ValueError("manifest identity is malformed")
    return tuple(int(field) for field in fields)


def _parse_parent_identity(value: str) -> ParentIdentity:
    device, inode = _parse_identity(value, 2)
    return device, inode


def _parse_file_identity(value: str) -> FileIdentity:
    device, inode, ctime_ns = _parse_identity(value, 3)
    return device, inode, ctime_ns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("path")
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("path")
    finalize.add_argument("parent_identity")
    finalize.add_argument("file_identity")
    finalize.add_argument("payload")
    verify = subparsers.add_parser("verify")
    verify.add_argument("path")
    verify.add_argument("parent_identity")
    verify.add_argument("file_identity")
    arguments = parser.parse_args()
    try:
        if arguments.command == "prepare":
            parent, file = prepare_manifest(Path(arguments.path))
            print(f"{_serialize_identity(parent)} {_serialize_identity(file)}")
        elif arguments.command == "finalize":
            finalize_manifest(
                Path(arguments.path),
                _parse_parent_identity(arguments.parent_identity),
                _parse_file_identity(arguments.file_identity),
                arguments.payload.encode(),
            )
        else:
            verify_manifest(
                Path(arguments.path),
                _parse_parent_identity(arguments.parent_identity),
                _parse_file_identity(arguments.file_identity),
            )
    except (OSError, ValueError):
        print("manifest_failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
