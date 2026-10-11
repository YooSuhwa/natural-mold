"""Uploads must verify a nonnull persisted Conversation parent before filesystem reads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.routers import uploads

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_real_jwt_attachment_rejects_persisted_foreign_parent_before_read(
    db: AsyncSession,
    raw_client: AsyncClient,
    private_scopes: PrivateScopes,
    org_fixture: OrgFixture,
    collector_case: CollectorCase,
) -> None:
    headers = org_fixture.session.headers()
    created = await raw_client.post(
        "/api/uploads",
        files={"file": ("fixture.txt", b"B06_DUMMY_ATTACHMENT", "text/plain")},
        headers=headers,
    )
    assert created.status_code == 201
    attachment_id = UUID(created.json()["id"])
    content_path = f"/api/uploads/{attachment_id}/content"
    orphan_control = await raw_client.get(content_path, headers=headers)
    assert orphan_control.status_code == 200
    await db.execute(
        update(MessageAttachment)
        .where(MessageAttachment.id == attachment_id)
        .values(conversation_id=collector_case.conversation.id)
    )
    await db.commit()
    parent_control = await raw_client.get(content_path, headers=headers)
    assert parent_control.status_code == 200
    foreign = private_scopes.second
    conversation = Conversation(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        agent_id=None,
        agent_name_snapshot="Foreign retained fixture",
        last_activity_at=datetime.now(UTC),
        title="Foreign fixture",
    )
    db.add(conversation)
    await db.flush()
    await db.execute(
        update(MessageAttachment)
        .where(MessageAttachment.id == attachment_id)
        .values(conversation_id=conversation.id)
    )
    await db.commit()
    read = Mock(wraps=uploads._read_file_prefix)
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(uploads, "_read_file_prefix", read)
        result = await raw_client.get(content_path, headers=headers)
    assert result.status_code == 404 and read.call_count == 0
