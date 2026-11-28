from __future__ import annotations

import asyncio
import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import settings
from app.services.artifact_paths import safe_storage_filename
from app.storage.paths import check_company_path, tenant_relative


@dataclass(frozen=True)
class StoredArtifactObject:
    storage_provider: str
    bucket: str | None
    object_key: str
    path: Path | None


class ArtifactStorageBackend(Protocol):
    async def put_file(
        self,
        *,
        conversation_id: uuid.UUID,
        artifact_id: uuid.UUID,
        version_number: int,
        display_name: str,
        source_path: Path,
        tenant_id: uuid.UUID | None = None,
        object_version_id: uuid.UUID | None = None,
    ) -> StoredArtifactObject: ...

    async def local_path(self, *, object_key: str, tenant_id: uuid.UUID | None = None) -> Path: ...


class ArtifactStorageBackendUnavailableError(RuntimeError):
    pass


def get_artifact_storage_backend() -> ArtifactStorageBackend:
    if settings.artifact_storage_backend == "local":
        return LocalArtifactStorageBackend()
    if settings.artifact_storage_backend == "s3":
        raise ArtifactStorageBackendUnavailableError(
            "S3 artifact storage is not implemented yet. Use ARTIFACT_STORAGE_BACKEND=local."
        )
    raise ArtifactStorageBackendUnavailableError(
        f"Unsupported artifact storage backend: {settings.artifact_storage_backend}"
    )


def artifact_object_key(
    *,
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    version_number: int,
    display_name: str,
    tenant_id: uuid.UUID | None,
    object_version_id: uuid.UUID | None = None,
) -> str:
    version_dir = f"v{version_number}"
    if object_version_id is not None:
        version_dir = f"{version_dir}/{object_version_id}"
    name = safe_storage_filename(display_name)
    return tenant_relative(
        tenant_id, f"conversations/{conversation_id}/{artifact_id}/{version_dir}/{name}"
    )


@contextmanager
def open_verified_object(path: Path, object_key: str) -> Iterator[BinaryIO]:
    """Open the canonical storage object without following redirected file or directory names."""
    root = Path(settings.artifact_storage_dir).resolve()
    key = Path(object_key)
    if key.is_absolute() or ".." in key.parts or path != root / key:
        raise ValueError("artifact storage identity mismatch")
    with ExitStack() as resources:
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            directory = os.open(root, flags | os.O_DIRECTORY)
            resources.callback(os.close, directory)
            for part in key.parts[:-1]:
                directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                resources.callback(os.close, directory)
            fd = os.open(key.name, flags | os.O_NONBLOCK, dir_fd=directory)
            source = resources.enter_context(os.fdopen(fd, "rb"))
        else:
            # On platforms without openat, recheck the resolved name and opened file identity.
            source = resources.enter_context(path.open("rb"))
            if path.resolve(strict=True) != path or not os.path.samestat(
                os.fstat(source.fileno()), path.lstat()
            ):
                raise ValueError("artifact storage identity mismatch")
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("artifact storage object is not a regular file")
        yield source


class LocalArtifactStorageBackend:
    provider = "local"

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir or settings.artifact_storage_dir)

    async def put_file(
        self,
        *,
        conversation_id: uuid.UUID,
        artifact_id: uuid.UUID,
        version_number: int,
        display_name: str,
        source_path: Path,
        tenant_id: uuid.UUID | None = None,
        object_version_id: uuid.UUID | None = None,
    ) -> StoredArtifactObject:
        """Copy into the company folder ``tenants/<tenant_id>/conversations/...`` (I-08)."""
        # A disconnected fenced writer may finish copying after its DB transaction
        # has rolled back. Consecutive owners can allocate the same version number.
        # Give each attempt its own object so only the committed row exposes bytes.
        object_key = artifact_object_key(
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            version_number=version_number,
            display_name=display_name,
            tenant_id=tenant_id,
            object_version_id=object_version_id,
        )
        target = (self.root_dir / object_key).resolve()
        root = self.root_dir.resolve()
        if not target.is_relative_to(root):
            raise ValueError("artifact object key escapes storage root")
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source_path, target)
        return StoredArtifactObject(
            storage_provider=self.provider,
            bucket=None,
            object_key=object_key,
            path=target,
        )

    async def local_path(self, *, object_key: str, tenant_id: uuid.UUID | None = None) -> Path:
        """Raises ``ForeignTenantPathError`` (a ``ValueError``) for another company's key."""
        target = (self.root_dir / object_key).resolve()
        if not target.is_relative_to(self.root_dir.resolve()):
            raise ValueError("artifact object key escapes storage root")
        check_company_path(target, self.root_dir, tenant_id)
        if not target.is_file():
            raise FileNotFoundError(target)
        return target
