"""Identity-pinned aggregate manifest writer."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from project_gate_runtime import JSONObject, ProjectGateError


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

    @classmethod
    def create(cls, path: Path) -> AggregateWriter:
        parent_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            parent_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            parent_flags |= os.O_NOFOLLOW
        try:
            parent_descriptor = os.open(path.parent, parent_flags)
        except OSError as error:
            raise ProjectGateError("unsafe_manifest") from error
        parent_metadata = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_metadata.st_mode):
            os.close(parent_descriptor)
            raise ProjectGateError("unsafe_manifest")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError as error:
            os.close(parent_descriptor)
            raise ProjectGateError("manifest_exists") from error
        except OSError as error:
            os.close(parent_descriptor)
            raise ProjectGateError("unsafe_manifest") from error
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            os.close(descriptor)
            os.unlink(path.name, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
            raise ProjectGateError("unsafe_manifest")
        return cls(
            path,
            descriptor,
            parent_descriptor,
            metadata.st_dev,
            metadata.st_ino,
            parent_metadata.st_dev,
            parent_metadata.st_ino,
        )

    def _unlink_owned(self) -> None:
        try:
            linked = os.stat(self.path.name, dir_fd=self.parent_descriptor, follow_symlinks=False)
        except OSError:
            return
        if (linked.st_dev, linked.st_ino) == (self.device, self.inode):
            os.unlink(self.path.name, dir_fd=self.parent_descriptor)
            os.fsync(self.parent_descriptor)

    def write(self, payload: JSONObject) -> None:
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        try:
            current = os.fstat(self.descriptor)
            parent = os.fstat(self.parent_descriptor)
            visible_parent = self.path.parent.stat(follow_symlinks=False)
            linked = os.stat(
                self.path.name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            self._unlink_owned()
            raise ProjectGateError("unsafe_manifest") from error
        identity = (current.st_dev, current.st_ino, current.st_nlink)
        linked_identity = (linked.st_dev, linked.st_ino, linked.st_nlink)
        parent_identity = (parent.st_dev, parent.st_ino)
        visible_parent_identity = (visible_parent.st_dev, visible_parent.st_ino)
        if (
            identity != (self.device, self.inode, 1)
            or linked_identity != identity
            or parent_identity != (self.parent_device, self.parent_inode)
            or visible_parent_identity != parent_identity
        ):
            self._unlink_owned()
            raise ProjectGateError("unsafe_manifest")
        try:
            offset = 0
            while offset < len(data):
                written = os.write(self.descriptor, data[offset:])
                if written <= 0:
                    raise ProjectGateError("manifest_write_failed")
                offset += written
            os.fsync(self.descriptor)
            os.fsync(self.parent_descriptor)
        except (OSError, ProjectGateError) as error:
            self._unlink_owned()
            raise ProjectGateError("manifest_write_failed") from error

    def close(self) -> None:
        os.close(self.descriptor)
        os.close(self.parent_descriptor)
