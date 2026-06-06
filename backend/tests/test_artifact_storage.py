from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.services.artifact_storage import (
    ArtifactStorageBackendUnavailableError,
    LocalArtifactStorageBackend,
    get_artifact_storage_backend,
)


def test_storage_factory_returns_local_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")

    storage = get_artifact_storage_backend()

    assert isinstance(storage, LocalArtifactStorageBackend)


def test_storage_factory_rejects_unimplemented_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage_backend", "s3")

    with pytest.raises(ArtifactStorageBackendUnavailableError, match="S3 artifact storage"):
        get_artifact_storage_backend()


@pytest.mark.asyncio
async def test_local_storage_copies_file_under_artifact_root(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("hello", encoding="utf-8")
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    conversation_id = uuid.uuid4()
    artifact_id = uuid.uuid4()

    stored = await storage.put_file(
        conversation_id=conversation_id,
        artifact_id=artifact_id,
        version_number=1,
        display_name="final.md",
        source_path=source,
    )

    assert stored.storage_provider == "local"
    assert stored.bucket is None
    assert stored.object_key.endswith("/v1/final.md")
    assert stored.path is not None
    assert stored.path.read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_local_path_requires_existing_file(tmp_path: Path) -> None:
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")

    with pytest.raises(FileNotFoundError):
        await storage.local_path(object_key="conversations/missing/artifact.md")
