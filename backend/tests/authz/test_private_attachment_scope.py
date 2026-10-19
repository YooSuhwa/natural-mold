"""Uploads stay within their persisted organization and conversation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.message_attachment import MessageAttachment
from app.routers.uploads import _get_owned_attachment
from app.services.chat.attachments import (
    link_attachments_to_conversation,
    link_attachments_to_message,
)
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


@pytest.fixture
async def private_upload(db: AsyncSession, private_scopes: PrivateScopes) -> MessageAttachment:
    context = private_scopes.first
    row = MessageAttachment(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        filename="fixture.txt",
        mime_type="text/plain",
        size_bytes=7,
        storage_path="unused-attachment-fixture",
        url="/api/uploads/fixture",
    )
    db.add(row)
    await db.commit()
    return row


async def test_upload_lookup_rejects_same_owner_foreign_company(
    db: AsyncSession, private_upload: MessageAttachment, private_scopes: PrivateScopes
) -> None:
    assert (
        await _get_owned_attachment(db, private_upload.id, private_scopes.first) is private_upload
    )
    with pytest.raises(AppError) as captured:
        await _get_owned_attachment(db, private_upload.id, private_scopes.second)
    assert captured.value.status == 404


async def test_link_rejects_foreign_conversation_before_changing_upload(
    db: AsyncSession,
    private_upload: MessageAttachment,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
) -> None:
    with pytest.raises(AppError) as captured:
        await link_attachments_to_conversation(
            db,
            context=private_scopes.second,
            conversation_id=collector_case.conversation.id,
            attachment_ids=[private_upload.id],
        )
    assert captured.value.status == 404
    assert private_upload.conversation_id is None


async def test_upload_cannot_be_reassigned_to_another_conversation(
    db: AsyncSession,
    private_upload: MessageAttachment,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
) -> None:
    context = private_scopes.first
    conversation = collector_case.conversation
    assert conversation.agent_id is not None
    other = await create_conversation(db, conversation.agent_id, context=context)
    await link_attachments_to_conversation(
        db, context=context, conversation_id=conversation.id, attachment_ids=[private_upload.id]
    )
    await link_attachments_to_conversation(
        db, context=context, conversation_id=other.id, attachment_ids=[private_upload.id]
    )
    await db.refresh(private_upload)
    assert private_upload.conversation_id == conversation.id
    assert (
        await link_attachments_to_message(
            db,
            context=context,
            conversation_id=other.id,
            attachment_ids=[private_upload.id],
            message_id="foreign-turn",
        )
        == 0
    )
    assert (
        await link_attachments_to_message(
            db,
            context=context,
            conversation_id=conversation.id,
            attachment_ids=[private_upload.id],
            message_id="own-turn",
        )
        == 1
    )
    await db.refresh(private_upload)
    assert private_upload.message_id == "own-turn"
