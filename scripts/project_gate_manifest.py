"""Identity-pinned aggregate manifest writer."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from postgres_manifest_io import (
    EvidenceDirectory,
    _verify_evidence_directory,
    close_evidence_directory,
    open_manifest_directory,
)
from project_gate_runtime import JSONObject, ProjectGateError


def _unlink_if_owned(name: str, parent_descriptor: int, device: int, inode: int) -> None:
    """Remove only the directory entry that still names the writer's inode."""
    try:
        linked = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError:
        return
    if (linked.st_dev, linked.st_ino) == (device, inode):
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)


@dataclass(frozen=True, slots=True)
class AggregateWriter:
    """Keep exclusive file and parent descriptors pinned until finalization."""

    path: Path
    descriptor: int
    parent_descriptor: int
    device: int
    inode: int
    parent_device: int
    parent_inode: int
    root_descriptor: int | None = None
    attempt_id: str | None = None
    head_sha: str | None = None
    root_path: Path | None = None
    root_device: int | None = None
    root_inode: int | None = None

    @classmethod
    def create(
        cls, path: Path, repo_root: Path | None = None, expected_head: str | None = None
    ) -> AggregateWriter:
        try:
            expected_evidence = (
                None
                if repo_root is None
                else repo_root / ".omo/evidence/project-restart-consolidated-roadmap"
            )
            production_path = expected_evidence is not None and path.absolute().is_relative_to(
                expected_evidence.absolute()
            )
            if not production_path:
                parent_descriptor = os.open(
                    path.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                )
                parent_metadata = os.fstat(parent_descriptor)
                evidence = EvidenceDirectory(
                    path.parent,
                    parent_descriptor,
                    parent_metadata.st_dev,
                    parent_metadata.st_ino,
                )
            else:
                if repo_root is None or expected_evidence is None:
                    raise ProjectGateError("unsafe_manifest")
                evidence = open_manifest_directory(
                    path,
                    expected_evidence,
                    repo_root,
                )
                parent_descriptor = evidence.descriptor
        except (OSError, RuntimeError) as error:
            raise ProjectGateError("unsafe_manifest") from error
        parent_metadata = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_metadata.st_mode) or (
            evidence.head_sha is not None and evidence.head_sha != expected_head
        ):
            close_evidence_directory(evidence)
            raise ProjectGateError("unsafe_manifest")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError as error:
            close_evidence_directory(evidence)
            raise ProjectGateError("manifest_exists") from error
        except OSError as error:
            close_evidence_directory(evidence)
            raise ProjectGateError("unsafe_manifest") from error
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            os.close(descriptor)
            _unlink_if_owned(path.name, parent_descriptor, metadata.st_dev, metadata.st_ino)
            close_evidence_directory(evidence)
            raise ProjectGateError("unsafe_manifest")
        return cls(
            path,
            descriptor,
            parent_descriptor,
            metadata.st_dev,
            metadata.st_ino,
            parent_metadata.st_dev,
            parent_metadata.st_ino,
            evidence.root_descriptor,
            evidence.attempt_id,
            evidence.head_sha,
            evidence.root_path,
            evidence.root_device,
            evidence.root_inode,
        )

    def _unlink_owned(self) -> None:
        _unlink_if_owned(self.path.name, self.parent_descriptor, self.device, self.inode)

    def _verify_owned_file(self) -> None:
        """Ensure the visible aggregate name still resolves to this safe open file."""
        try:
            current = os.fstat(self.descriptor)
            linked = os.stat(
                self.path.name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ProjectGateError("unsafe_manifest") from error
        expected = (self.device, self.inode)
        current_identity = (current.st_dev, current.st_ino)
        linked_identity = (linked.st_dev, linked.st_ino)
        safe_current = (
            stat.S_ISREG(current.st_mode)
            and current.st_uid == os.geteuid()
            and current.st_nlink == 1
            and not current.st_mode & 0o022
        )
        safe_linked = (
            stat.S_ISREG(linked.st_mode)
            and linked.st_uid == os.geteuid()
            and linked.st_nlink == 1
            and not linked.st_mode & 0o022
        )
        if (
            not safe_current
            or not safe_linked
            or current_identity != expected
            or linked_identity != expected
        ):
            raise ProjectGateError("unsafe_manifest")

    def verify_binding(self) -> None:
        """Revalidate the reserved aggregate parent before starting a child."""
        try:
            _verify_evidence_directory(
                EvidenceDirectory(
                    self.path.parent,
                    self.parent_descriptor,
                    self.parent_device,
                    self.parent_inode,
                    self.root_descriptor,
                    self.attempt_id,
                    self.head_sha,
                    self.root_path,
                    self.root_device,
                    self.root_inode,
                )
            )
        except (OSError, RuntimeError) as error:
            raise ProjectGateError("unsafe_manifest") from error

    def write(self, payload: JSONObject) -> None:
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.verify_binding()
        try:
            parent = os.fstat(self.parent_descriptor)
            visible_parent = self.path.parent.stat(follow_symlinks=False)
        except OSError as error:
            self._unlink_owned()
            raise ProjectGateError("unsafe_manifest") from error
        parent_identity = (parent.st_dev, parent.st_ino)
        visible_parent_identity = (visible_parent.st_dev, visible_parent.st_ino)
        if (
            parent_identity != (self.parent_device, self.parent_inode)
            or visible_parent_identity != parent_identity
        ):
            self._unlink_owned()
            raise ProjectGateError("unsafe_manifest")
        try:
            self._verify_owned_file()
        except ProjectGateError:
            self._unlink_owned()
            raise
        try:
            offset = 0
            while offset < len(data):
                written = os.write(self.descriptor, data[offset:])
                if written <= 0:
                    raise ProjectGateError("manifest_write_failed")
                offset += written
            os.fsync(self.descriptor)
            _verify_evidence_directory(
                EvidenceDirectory(
                    self.path.parent,
                    self.parent_descriptor,
                    self.parent_device,
                    self.parent_inode,
                    self.root_descriptor,
                    self.attempt_id,
                    self.head_sha,
                    self.root_path,
                    self.root_device,
                    self.root_inode,
                )
            )
            self._verify_owned_file()
            os.fsync(self.parent_descriptor)
            self.verify_binding()
            self._verify_owned_file()
        except (OSError, ProjectGateError) as error:
            self._unlink_owned()
            raise ProjectGateError("manifest_write_failed") from error

    def close(self) -> None:
        os.close(self.descriptor)
        os.close(self.parent_descriptor)
        if self.root_descriptor is not None:
            os.close(self.root_descriptor)
