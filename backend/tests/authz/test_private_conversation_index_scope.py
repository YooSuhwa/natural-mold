"""Conversation pagination and cleanup require the conversation's private scope."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.services.chat.conversations import delete_conversation, list_global_conversations_page

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_global_conversation_page_is_private_to_selected_company(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    own, _, _ = await list_global_conversations_page(db, private_scopes.first, limit=30)
    assert [row.id for row in own] == [collector_case.conversation.id]
    foreign, _, _ = await list_global_conversations_page(db, private_scopes.second, limit=30)
    assert foreign == []


async def test_foreign_conversation_delete_does_not_touch_checkpoint_or_files(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    checkpoint = AsyncMock()
    with (
        patch("app.agent_runtime.checkpointer.delete_thread", checkpoint),
        pytest.raises(AppError) as captured,
    ):
        await delete_conversation(db, collector_case.conversation, private_scopes.second)
    assert captured.value.status == 404
    checkpoint.assert_not_awaited()


async def test_agentless_history_cleanup_uses_conversation_owner(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    conversation = collector_case.conversation
    conversation.agent_id = None
    conversation.agent_lifecycle_state = "purged"
    conversation.agent_purged_at = datetime.now(UTC)
    await db.flush()
    checkpoint = AsyncMock()
    with (
        patch("app.agent_runtime.checkpointer.delete_thread", checkpoint),
        patch("app.agent_runtime.offload_storage.delete_conversation_offloads") as offloads,
    ):
        await delete_conversation(db, conversation, private_scopes.first)
    checkpoint.assert_awaited_once_with(str(conversation.id))
    assert offloads.call_args.kwargs["owner_id"] == str(conversation.user_id)
