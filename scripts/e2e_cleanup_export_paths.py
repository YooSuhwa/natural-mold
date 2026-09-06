"""Descriptor-relative E2E export validation and native exporter filesystem helper."""

# allow: SIZE_OK - checker and exporter must share one audited dir_fd implementation.

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TypedDict

from e2e_cleanup_contract import require, string
from postgres_cleanup_checker import ManifestValidationError

MAX_FILE_BYTES: Final = 20 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 50 * 1024 * 1024
SAFE_PART: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_TEST_SWAP_ENV: Final = "MOLDY_E2E_FS_TEST_SWAP_JSON"


class NativeRequest(TypedDict):
    command: str
    root: str
    sources: list[dict[str, str]]
    selected: list[dict[str, str | int]]
    destination: str
    files: list[dict[str, str]]
    target: str
    payload: str


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    change_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> _Identity:
        return cls(metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns)


class _PinnedDirectory:
    """Own a no-follow descriptor chain from filesystem root to one directory."""

    def __init__(self, directory: Path) -> None:
        absolute = directory.absolute()
        self._fds: list[int] = [os.open("/", _DIRECTORY_FLAGS)]
        self._names: list[str] = []
        try:
            for part in absolute.parts[1:]:
                self._fds.append(os.open(part, _DIRECTORY_FLAGS, dir_fd=self._fds[-1]))
                self._names.append(part)
            self._identities = tuple(_Identity.from_stat(os.fstat(fd)) for fd in self._fds)
            self.validate("export_directory")
        except (OSError, ManifestValidationError):
            self.close()
            raise

    @property
    def fd(self) -> int:
        return self._fds[-1]

    def validate(self, reason: str, *, require_unchanged: bool = True) -> None:
        for index, (descriptor, expected) in enumerate(
            zip(self._fds, self._identities, strict=True)
        ):
            current = os.fstat(descriptor)
            require(
                stat.S_ISDIR(current.st_mode)
                and (current.st_dev, current.st_ino) == (expected.device, expected.inode),
                reason,
            )
            if index:
                named = os.stat(
                    self._names[index - 1],
                    dir_fd=self._fds[index - 1],
                    follow_symlinks=False,
                )
                is_trust_root = index == len(self._fds) - 1
                require(
                    stat.S_ISDIR(named.st_mode)
                    and (named.st_dev, named.st_ino) == (expected.device, expected.inode)
                    and (
                        not (require_unchanged and is_trust_root)
                        or named.st_ctime_ns == expected.change_ns
                    ),
                    reason,
                )

    def refresh(self) -> None:
        self._identities = tuple(_Identity.from_stat(os.fstat(fd)) for fd in self._fds)

    def close(self) -> None:
        for descriptor in reversed(getattr(self, "_fds", [])):
            os.close(descriptor)
        self._fds = []

    def __enter__(self) -> _PinnedDirectory:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def safe_relative(raw: object, reason: str) -> PurePosixPath:
    """Parse a portable bounded relative artifact path."""
    value = string(raw, reason)
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {".", ".."} and SAFE_PART.fullmatch(part) for part in path.parts),
        reason,
    )
    return path


def _open_child(parent_fd: int, name: str, reason: str) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ManifestValidationError(reason) from error


def _open_relative_directory(root_fd: int, relative: PurePosixPath, reason: str) -> list[int]:
    descriptors: list[int] = []
    parent_fd = root_fd
    try:
        for part in relative.parts:
            descriptor = _open_child(parent_fd, part, reason)
            descriptors.append(descriptor)
            parent_fd = descriptor
        return descriptors
    except ManifestValidationError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _validate_relative_chain(
    root_fd: int,
    relative: PurePosixPath,
    descriptors: list[int],
    reason: str,
    expected_identities: tuple[_Identity, ...] | None = None,
    *,
    require_unchanged: bool = True,
) -> None:
    parent_fd = root_fd
    identities = expected_identities or tuple(
        _Identity.from_stat(os.fstat(descriptor)) for descriptor in descriptors
    )
    for part, descriptor, expected in zip(relative.parts, descriptors, identities, strict=True):
        descriptor_metadata = os.fstat(descriptor)
        current = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
        descriptor_identity = (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        named_identity = (current.st_dev, current.st_ino)
        expected_identity = (expected.device, expected.inode)
        require(
            stat.S_ISDIR(current.st_mode)
            and descriptor_identity == expected_identity
            and named_identity == expected_identity
            and (
                not require_unchanged
                or (
                    descriptor_metadata.st_ctime_ns == expected.change_ns
                    and current.st_ctime_ns == expected.change_ns
                )
            ),
            reason,
        )
        parent_fd = descriptor


def _walk_fd(
    directory_fd: int, prefix: PurePosixPath = PurePosixPath()
) -> list[tuple[PurePosixPath, os.stat_result]]:
    discovered: list[tuple[PurePosixPath, os.stat_result]] = []
    for name in sorted(os.listdir(directory_fd)):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        relative = prefix / name
        if stat.S_ISLNK(metadata.st_mode):
            raise ManifestValidationError("symbolic_link")
        if stat.S_ISDIR(metadata.st_mode):
            child = _open_child(directory_fd, name, "export_tree")
            try:
                discovered.extend(_walk_fd(child, relative))
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                require(
                    (current.st_dev, current.st_ino) == (metadata.st_dev, metadata.st_ino),
                    "export_tree",
                )
            finally:
                os.close(child)
        else:
            require(stat.S_ISREG(metadata.st_mode), "regular_file")
            require(metadata.st_nlink == 1, "hard_link")
            require(metadata.st_size <= MAX_FILE_BYTES, "export_file_size")
            discovered.append((relative, metadata))
    return discovered


def _read_at(
    root_fd: int,
    relative: PurePosixPath,
    expected: os.stat_result | None = None,
) -> bytes:
    parents = _open_relative_directory(
        root_fd, PurePosixPath(*relative.parts[:-1]), "export_file_read"
    )
    parent_fd = parents[-1] if parents else root_fd
    descriptor = -1
    try:
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(descriptor)
        require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
            "export_file_type",
        )
        require(before.st_size <= MAX_FILE_BYTES, "export_file_size")
        if expected is not None:
            require(
                (before.st_dev, before.st_ino, before.st_size)
                == (expected.st_dev, expected.st_ino, expected.st_size),
                "export_file_changed",
            )
        content = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            content.extend(chunk)
        after = os.fstat(descriptor)
        current = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
        require(
            len(content) == before.st_size
            and (after.st_dev, after.st_ino, after.st_size)
            == (before.st_dev, before.st_ino, before.st_size)
            and (current.st_dev, current.st_ino, current.st_size)
            == (before.st_dev, before.st_ino, before.st_size),
            "export_file_changed",
        )
        return bytes(content)
    except OSError as error:
        raise ManifestValidationError("export_file_read") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        for parent in reversed(parents):
            os.close(parent)


def _test_swap(phase: str) -> None:
    raw = os.environ.get(_TEST_SWAP_ENV)
    if raw is None:
        return
    if "PYTEST_CURRENT_TEST" not in os.environ and os.environ.get("VITEST") != "true":
        raise ManifestValidationError("test_swap_disabled")
    try:
        request = json.loads(raw)
        if request.get("phase") != phase:
            return
        target = Path(request["target"])
        replacement = Path(request["replacement"])
        saved = Path(request["saved"])
        target.rename(saved)
        replacement.rename(target)
        target.rename(replacement)
        saved.rename(target)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ManifestValidationError("test_swap") from error


def read_regular(path: Path) -> bytes:
    """Read one unlinked regular file through a pinned absolute ancestor chain."""
    try:
        with _PinnedDirectory(path.parent) as parent:
            _test_swap("checker_read")
            parent.validate("export_file_changed")
            return _read_at(parent.fd, PurePosixPath(path.name))
    except OSError as error:
        raise ManifestValidationError("export_file_read") from error


def walk_export(directory: Path) -> set[str]:
    """Return every relative leaf using descriptor-relative traversal only."""
    try:
        with _PinnedDirectory(directory) as pinned:
            _test_swap("checker_walk")
            pinned.validate("export_tree")
            return {relative.as_posix() for relative, _metadata in _walk_fd(pinned.fd)}
    except OSError as error:
        raise ManifestValidationError("export_directory") from error


def safe_export_directory(repository_root: Path, relative: PurePosixPath) -> Path:
    """Validate an export directory through pinned non-symlink ancestors."""
    directory = repository_root.joinpath(*relative.parts)
    try:
        with _PinnedDirectory(directory) as pinned:
            pinned.validate("export_directory")
    except OSError as error:
        raise ManifestValidationError("export_directory") from error
    return directory


def _native_list(request: NativeRequest) -> dict[str, object]:
    output: list[dict[str, str | int]] = []
    with _PinnedDirectory(Path(request["root"])) as pinned:
        for source in request["sources"]:
            relative = safe_relative(source["relative"], "export_directory")
            descriptors = _open_relative_directory(pinned.fd, relative, "export_directory")
            try:
                for leaf, metadata in _walk_fd(descriptors[-1]):
                    output.append(
                        {
                            "kind": source["kind"],
                            "path": leaf.as_posix(),
                            "device": str(metadata.st_dev),
                            "inode": str(metadata.st_ino),
                            "size": metadata.st_size,
                        }
                    )
                _validate_relative_chain(pinned.fd, relative, descriptors, "export_tree")
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
        pinned.validate("export_tree")
    return {"files": output}


def _native_read(request: NativeRequest) -> dict[str, object]:
    sources = {
        item["kind"]: safe_relative(item["relative"], "export_directory")
        for item in request["sources"]
    }
    output: list[dict[str, str]] = []
    with _PinnedDirectory(Path(request["root"])) as pinned:
        for selected in request["selected"]:
            source_relative = sources[str(selected["kind"])]
            descriptors = _open_relative_directory(pinned.fd, source_relative, "export_directory")
            try:
                expected = os.stat(
                    str(selected["path"]),
                    dir_fd=descriptors[-1],
                    follow_symlinks=False,
                )
                require(
                    str(expected.st_dev) == selected["device"]
                    and str(expected.st_ino) == selected["inode"]
                    and expected.st_size == selected["size"],
                    "export_file_changed",
                )
                chain_before = tuple(
                    _Identity.from_stat(os.fstat(descriptor)) for descriptor in descriptors
                )
                _test_swap("source_read")
                pinned.validate("export_file_changed")
                _validate_relative_chain(
                    pinned.fd,
                    source_relative,
                    descriptors,
                    "export_file_changed",
                    chain_before,
                )
                content = _read_at(
                    descriptors[-1],
                    safe_relative(selected["path"], "export_file_path"),
                    expected,
                )
                pinned.validate("export_file_changed")
                _validate_relative_chain(
                    pinned.fd,
                    source_relative,
                    descriptors,
                    "export_file_changed",
                    chain_before,
                )
                output.append(
                    {
                        "kind": str(selected["kind"]),
                        "path": str(selected["path"]),
                        "content": base64.b64encode(content).decode(),
                    }
                )
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
    return {"files": output}


def _mkdir_chain(root_fd: int, relative: PurePosixPath) -> list[int]:
    descriptors: list[int] = []
    parent_fd = root_fd
    for part in relative.parts:
        with contextlib.suppress(FileExistsError):
            os.mkdir(part, mode=0o700, dir_fd=parent_fd)
        descriptor = _open_child(parent_fd, part, "export_directory")
        descriptors.append(descriptor)
        parent_fd = descriptor
    return descriptors


def _write_tree(directory_fd: int, files: list[dict[str, str]]) -> None:
    for item in files:
        relative = safe_relative(item["path"], "export_file_path")
        parents = _mkdir_chain(directory_fd, PurePosixPath(*relative.parts[:-1]))
        parent_fd = parents[-1] if parents else directory_fd
        descriptor = -1
        try:
            descriptor = os.open(
                relative.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            view = memoryview(base64.b64decode(item["content"], validate=True))
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            for parent in reversed(parents):
                os.close(parent)


def _remove_tree_at(parent_fd: int, name: str) -> None:
    child = _open_child(parent_fd, name, "export_directory")
    try:
        for entry in os.listdir(child):  # noqa: PTH208 - fd-relative traversal is required
            metadata = os.stat(entry, dir_fd=child, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                _remove_tree_at(child, entry)
            else:
                os.unlink(entry, dir_fd=child)
    finally:
        os.close(child)
    os.rmdir(name, dir_fd=parent_fd)


def _native_publish(request: NativeRequest) -> dict[str, object]:
    destination = safe_relative(request["destination"], "export_directory")
    parent_relative = PurePosixPath(*destination.parts[:-1])
    with _PinnedDirectory(Path(request["root"])) as root:
        parents = _mkdir_chain(root.fd, parent_relative)
        parent_fd = parents[-1] if parents else root.fd
        staging = f".staging-{os.getpid()}-{os.urandom(8).hex()}"
        published = False
        try:
            os.mkdir(staging, mode=0o700, dir_fd=parent_fd)
            staging_fd = _open_child(parent_fd, staging, "export_directory")
            try:
                _write_tree(staging_fd, request["files"])
                os.fsync(staging_fd)
            finally:
                os.close(staging_fd)
            parent_before = _Identity.from_stat(os.fstat(parent_fd))
            root.refresh()
            chain_before = tuple(
                _Identity.from_stat(os.fstat(descriptor)) for descriptor in parents
            )
            _test_swap("destination_publish")
            root.validate("export_directory")
            _validate_relative_chain(
                root.fd,
                parent_relative,
                parents,
                "export_directory",
                chain_before,
            )
            require(
                _Identity.from_stat(os.fstat(parent_fd)) == parent_before,
                "export_directory",
            )
            try:
                os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ManifestValidationError("export_destination_exists")
            os.rename(
                staging,
                destination.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            published = True
            require(
                (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino)
                == (parent_before.device, parent_before.inode),
                "export_directory",
            )
            root.validate("export_directory", require_unchanged=False)
            _validate_relative_chain(
                root.fd,
                parent_relative,
                parents,
                "export_directory",
                chain_before,
                require_unchanged=False,
            )
            published_stat = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
            require(stat.S_ISDIR(published_stat.st_mode), "export_directory")
            os.fsync(parent_fd)
        finally:
            if not published:
                with contextlib.suppress(FileNotFoundError, ManifestValidationError):
                    _remove_tree_at(parent_fd, staging)
            for descriptor in reversed(parents):
                os.close(descriptor)
    return {"ok": True}


def _native_receipt(request: NativeRequest) -> dict[str, object]:
    target = safe_relative(request["target"], "export_file_path")
    parent_relative = PurePosixPath(*target.parts[:-1])
    with _PinnedDirectory(Path(request["root"])) as root:
        parents = _mkdir_chain(root.fd, parent_relative)
        parent_fd = parents[-1] if parents else root.fd
        staging = f".{target.name}.staging-{os.getpid()}-{os.urandom(8).hex()}"
        descriptor = os.open(
            staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            view = memoryview(base64.b64decode(request["payload"], validate=True))
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        published = False
        try:
            parent_before = _Identity.from_stat(os.fstat(parent_fd))
            root.refresh()
            chain_before = tuple(
                _Identity.from_stat(os.fstat(descriptor)) for descriptor in parents
            )
            _test_swap("receipt_publish")
            root.validate("export_directory")
            _validate_relative_chain(
                root.fd,
                parent_relative,
                parents,
                "export_directory",
                chain_before,
            )
            require(
                _Identity.from_stat(os.fstat(parent_fd)) == parent_before,
                "export_directory",
            )
            try:
                os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ManifestValidationError("export_destination_exists")
            os.rename(staging, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            published = True
            require(
                (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino)
                == (parent_before.device, parent_before.inode),
                "export_directory",
            )
            root.validate("export_directory", require_unchanged=False)
            _validate_relative_chain(
                root.fd,
                parent_relative,
                parents,
                "export_directory",
                chain_before,
                require_unchanged=False,
            )
            os.fsync(parent_fd)
        finally:
            if not published:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(staging, dir_fd=parent_fd)
            for descriptor in reversed(parents):
                os.close(descriptor)
    return {"ok": True}


def _native_remove(request: NativeRequest) -> dict[str, object]:
    destination = safe_relative(request["destination"], "export_directory")
    parent_relative = PurePosixPath(*destination.parts[:-1])
    with _PinnedDirectory(Path(request["root"])) as root:
        parents = _open_relative_directory(root.fd, parent_relative, "export_directory")
        try:
            parent_fd = parents[-1] if parents else root.fd
            _remove_tree_at(parent_fd, destination.name)
            os.fsync(parent_fd)
        finally:
            for descriptor in reversed(parents):
                os.close(descriptor)
    return {"ok": True}


def _native_main() -> int:
    try:
        request: NativeRequest = json.loads(sys.stdin.read())
        handlers = {
            "list": _native_list,
            "read": _native_read,
            "publish": _native_publish,
            "receipt": _native_receipt,
            "remove": _native_remove,
        }
        command = request.get("command")
        if command not in handlers:
            raise ManifestValidationError("native_command")
        sys.stdout.write(json.dumps(handlers[command](request), separators=(",", ":")))
        return 0
    except ManifestValidationError as error:
        sys.stdout.write(json.dumps({"error": str(error)}, separators=(",", ":")))
        return 70
    except (KeyError, TypeError, ValueError, OSError):
        return 70


if __name__ == "__main__":
    raise SystemExit(_native_main())
