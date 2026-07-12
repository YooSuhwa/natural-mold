"""Artifact content and storage access (BE-S8 split).

Text preview reads, download path resolution, and file hashing helpers on
top of the artifact storage backend.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.schemas.artifact import ArtifactTextContent
from app.services.artifact_storage import ArtifactStorageBackend, get_artifact_storage_backend
from app.services.artifacts.errors import ArtifactNotFoundError
from app.services.artifacts.library import _get_owned_artifact_with_version


async def read_artifact_text_content(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
    storage: ArtifactStorageBackend | None = None,
    max_bytes: int | None = None,
) -> ArtifactTextContent:
    artifact, version = await _get_owned_artifact_with_version(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    storage = storage or get_artifact_storage_backend()
    path = await _storage_local_path(storage, object_key=version.object_key)
    max_bytes = max_bytes or settings.artifact_preview_max_text_bytes
    try:
        data = await asyncio.to_thread(_read_file_prefix, path, max_bytes + 1)
    except FileNotFoundError as exc:
        raise ArtifactNotFoundError("artifact object not found") from exc
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return ArtifactTextContent(
        text=text,
        truncated=truncated,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
    )


async def get_artifact_download_path(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
    storage: ArtifactStorageBackend | None = None,
) -> tuple[ConversationArtifact, ArtifactVersion, Path]:
    artifact, version = await _get_owned_artifact_with_version(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    storage = storage or get_artifact_storage_backend()
    path = await _storage_local_path(storage, object_key=version.object_key)
    return artifact, version, path


def _read_file_prefix(path: Path, byte_count: int) -> bytes:
    with path.open("rb") as file_obj:
        return file_obj.read(byte_count)


async def _storage_local_path(storage: ArtifactStorageBackend, *, object_key: str) -> Path:
    try:
        return await storage.local_path(object_key=object_key)
    except (FileNotFoundError, ValueError) as exc:
        raise ArtifactNotFoundError("artifact object not found") from exc


async def _sha256_file(path: Path) -> str:
    return await asyncio.to_thread(_sha256_file_sync, path)


def _sha256_file_sync(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
