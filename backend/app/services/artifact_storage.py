from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.services.artifact_paths import safe_storage_filename


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
    ) -> StoredArtifactObject:
        ...

    async def local_path(self, *, object_key: str) -> Path:
        ...


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
    ) -> StoredArtifactObject:
        safe_name = safe_storage_filename(display_name)
        object_key = (
            f"conversations/{conversation_id}/{artifact_id}/v{version_number}/{safe_name}"
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

    async def local_path(self, *, object_key: str) -> Path:
        target = (self.root_dir / object_key).resolve()
        if not target.is_relative_to(self.root_dir.resolve()):
            raise ValueError("artifact object key escapes storage root")
        if not target.is_file():
            raise FileNotFoundError(target)
        return target
