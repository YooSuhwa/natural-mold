from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.message_attachment import MessageAttachment
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from tests.test_chat_resource_context_support import (
    resource_context_resolver,
    seed_resource_context,
)


@pytest.mark.asyncio
async def test_resolve_text_file_uses_owned_attachment_and_bounded_snapshot(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    body = "hello from attachment"
    path = tmp_path / "notes.txt"
    path.write_text(body, encoding="utf-8")
    attachment = MessageAttachment(
        user_id=seeded.user.id,
        conversation_id=seeded.conversation.id,
        message_id=str(uuid.uuid4()),
        filename="notes.txt",
        mime_type="text/plain",
        size_bytes=path.stat().st_size,
        storage_path=str(path),
        url="/api/uploads/notes",
    )
    db.add(attachment)
    await db.flush()

    frozen = await resource_context_resolver(db, seeded, tmp_path).resolve(
        ChatResourceContextRequest(
            resources=[ChatResourceReference(kind="file", id=attachment.id, label="spoofed")]
        )
    )

    assert frozen.resources[0].text == body
    assert frozen.resources[0].resolved_label == "notes.txt"
    assert frozen.resources[0].label == "spoofed"


@pytest.mark.asyncio
async def test_resolve_binary_file_uses_metadata_descriptor_not_raw_bytes(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    raw = b"\x00\xffprivate-binary"
    path = tmp_path / "image.png"
    path.write_bytes(raw)
    attachment = MessageAttachment(
        user_id=seeded.user.id,
        conversation_id=seeded.conversation.id,
        message_id=str(uuid.uuid4()),
        filename="image.png",
        mime_type="image/png",
        size_bytes=len(raw),
        storage_path=str(path),
        url="/api/uploads/image",
    )
    db.add(attachment)
    await db.flush()

    frozen = await resource_context_resolver(db, seeded, tmp_path).resolve(
        ChatResourceContextRequest(resources=[ChatResourceReference(kind="file", id=attachment.id)])
    )

    assert "image/png" in frozen.resources[0].text
    assert raw.hex() not in frozen.resources[0].text
    assert frozen.resources[0].content_sha256 == hashlib.sha256(raw).hexdigest()
    assert frozen.resources[0].content_available is False


@pytest.mark.asyncio
async def test_resolve_artifact_freezes_exact_version_and_does_not_substitute_latest(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    artifact = ConversationArtifact(
        user_id=seeded.user.id,
        agent_id=seeded.agent.id,
        conversation_id=seeded.conversation.id,
        assistant_msg_id=str(uuid.uuid4()),
        logical_path="report.txt",
        display_name="report.txt",
        mime_type="text/plain",
        artifact_kind="document",
        size_bytes=3,
        sha256="old-hash",
        status="ready",
    )
    db.add(artifact)
    await db.flush()
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    old = ArtifactVersion(
        artifact_id=artifact.id,
        version_number=1,
        object_key="old.txt",
        original_filename="report.txt",
        size_bytes=3,
        sha256=hashlib.sha256(b"old").hexdigest(),
    )
    new = ArtifactVersion(
        artifact_id=artifact.id,
        version_number=2,
        object_key="new.txt",
        original_filename="report.txt",
        size_bytes=3,
        sha256=hashlib.sha256(b"new").hexdigest(),
    )
    db.add_all([old, new])
    await db.flush()
    artifact.current_version_id = new.id
    await db.flush()

    frozen = await resource_context_resolver(db, seeded, tmp_path).resolve(
        ChatResourceContextRequest(
            resources=[ChatResourceReference(kind="artifact", id=artifact.id, version_id=old.id)]
        )
    )

    assert frozen.resources[0].version_id == old.id
    assert frozen.resources[0].text == "old"
