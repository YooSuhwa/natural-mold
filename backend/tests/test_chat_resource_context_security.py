from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_attachment import MessageAttachment
from app.models.user import User
from app.schemas.chat_resource_context import (
    MAX_RESOURCE_CONTEXT_ITEM_BYTES,
    ChatResourceContextRequest,
    ChatResourceReference,
)
from app.services.chat_resource_context_errors import (
    ResourceContextLimitError,
    ResourceContextNotFoundError,
)
from tests.test_chat_resource_context_support import (
    resource_context_resolver,
    seed_resource_context,
)


@pytest.mark.asyncio
async def test_foreign_resource_is_indistinguishable_from_missing_resource(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    foreign = User(id=uuid.uuid4(), email="foreign@test.com", name="Foreign")
    db.add(foreign)
    await db.flush()
    path = tmp_path / "foreign.txt"
    path.write_text("foreign", encoding="utf-8")
    attachment = MessageAttachment(
        user_id=foreign.id,
        filename="foreign.txt",
        mime_type="text/plain",
        size_bytes=7,
        storage_path=str(path),
        url="/api/uploads/foreign",
    )
    db.add(attachment)
    await db.flush()
    resolver = resource_context_resolver(db, seeded, tmp_path)

    with pytest.raises(ResourceContextNotFoundError) as foreign_error:
        await resolver.resolve(
            ChatResourceContextRequest(
                resources=[ChatResourceReference(kind="file", id=attachment.id)]
            )
        )
    with pytest.raises(ResourceContextNotFoundError) as missing_error:
        await resolver.resolve(
            ChatResourceContextRequest(
                resources=[ChatResourceReference(kind="file", id=uuid.uuid4())]
            )
        )

    assert str(foreign_error.value) == str(missing_error.value)


@pytest.mark.asyncio
async def test_dispatch_reauthorization_keeps_snapshot_when_content_changed(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    path = tmp_path / "notes.txt"
    path.write_text("enqueue snapshot", encoding="utf-8")
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
    resolver = resource_context_resolver(db, seeded, tmp_path)
    frozen = await resolver.resolve(
        ChatResourceContextRequest(resources=[ChatResourceReference(kind="file", id=attachment.id)])
    )
    path.write_text("changed after enqueue", encoding="utf-8")

    authorized = await resolver.reauthorize(frozen)

    assert authorized.resources[0].text == "enqueue snapshot"


@pytest.mark.asyncio
async def test_dispatch_reauthorization_fails_after_resource_deleted(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    path = tmp_path / "notes.txt"
    path.write_text("snapshot", encoding="utf-8")
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
    resolver = resource_context_resolver(db, seeded, tmp_path)
    frozen = await resolver.resolve(
        ChatResourceContextRequest(resources=[ChatResourceReference(kind="file", id=attachment.id)])
    )
    await db.delete(attachment)
    await db.flush()

    with pytest.raises(ResourceContextNotFoundError):
        await resolver.reauthorize(frozen)


@pytest.mark.asyncio
async def test_total_resolved_context_limit_is_enforced(db: AsyncSession, tmp_path: Path) -> None:
    seeded = await seed_resource_context(db)
    references: list[ChatResourceReference] = []
    for index in range(5):
        path = tmp_path / f"large-{index}.txt"
        path.write_text("x" * (32 * 1024), encoding="utf-8")
        attachment = MessageAttachment(
            user_id=seeded.user.id,
            conversation_id=seeded.conversation.id,
            message_id=str(uuid.uuid4()),
            filename=path.name,
            mime_type="text/plain",
            size_bytes=path.stat().st_size,
            storage_path=str(path),
            url=f"/api/uploads/{index}",
        )
        db.add(attachment)
        await db.flush()
        references.append(ChatResourceReference(kind="file", id=attachment.id))

    with pytest.raises(ResourceContextLimitError):
        await resource_context_resolver(db, seeded, tmp_path).resolve(
            ChatResourceContextRequest(resources=references)
        )


@pytest.mark.asyncio
async def test_each_resolved_item_is_bounded_by_utf8_bytes(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    path = tmp_path / "multibyte.txt"
    path.write_text("가" * MAX_RESOURCE_CONTEXT_ITEM_BYTES, encoding="utf-8")
    attachment = MessageAttachment(
        user_id=seeded.user.id,
        conversation_id=seeded.conversation.id,
        message_id=str(uuid.uuid4()),
        filename=path.name,
        mime_type="text/plain",
        size_bytes=path.stat().st_size,
        storage_path=str(path),
        url="/api/uploads/multibyte",
    )
    db.add(attachment)
    await db.flush()

    frozen = await resource_context_resolver(db, seeded, tmp_path).resolve(
        ChatResourceContextRequest(resources=[ChatResourceReference(kind="file", id=attachment.id)])
    )

    assert len(frozen.resources[0].text.encode("utf-8")) <= MAX_RESOURCE_CONTEXT_ITEM_BYTES
