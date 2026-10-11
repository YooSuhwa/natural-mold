"""Completed branch activation checks the captured actor before checkpoint work."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.worker_conversation_scope import WorkerConversationTarget
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.services import conversation_run_worker as worker
from app.services.chat.conversations import create_conversation
from app.services.thread_branch_service import MessageTree
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("boundary", ["revoked", "moved"])
async def test_completed_branch_activation_rechecks_current_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    boundary: Literal["revoked", "moved"],
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    await db.commit()
    conversation_id = conversation.id
    calls: list[str] = []

    async def checkpoint_tree(
        _checkpointer: None, thread_id: str, active_checkpoint_id: str | None = None
    ) -> MessageTree:
        calls.append(thread_id)
        return MessageTree(
            nodes=[], active_tip_message_id=None, active_checkpoint_id="f1-dummy-new-leaf"
        )

    monkeypatch.setattr(worker, "async_session", TestSession)
    monkeypatch.setattr(worker, "get_checkpointer", lambda: None)
    monkeypatch.setattr(worker.thread_branch_service, "build_message_tree", checkpoint_tree)
    await worker._activate_latest_branch_leaf_if_needed(
        target=WorkerConversationTarget(conversation_id, own),
        moldy_source="regenerate",
        final_status="completed",
    )
    await db.refresh(conversation)
    assert conversation.active_branch_checkpoint_id == "f1-dummy-new-leaf"
    assert len(calls) == 1
    conversation.active_branch_checkpoint_id = "f1-original-leaf"
    match boundary:
        case "revoked":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
            )
        case "moved":
            other = private_scopes.second
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(org_id=other.org_id, tenant_id=other.tenant_id)
                .execution_options(synchronize_session=False)
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    calls.clear()
    await worker._activate_latest_branch_leaf_if_needed(
        target=WorkerConversationTarget(conversation_id, own),
        moldy_source="regenerate",
        final_status="completed",
    )
    stored = await db.scalar(
        select(Conversation.active_branch_checkpoint_id).where(Conversation.id == conversation_id)
    )
    observed = (len(calls), stored)
    await db.rollback()
    assert observed == (0, "f1-original-leaf"), (
        f"revoked completion checkpoint and content mutation: {observed}"
    )


async def test_branch_activation_rechecks_actor_after_checkpoint_read(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    conversation.active_branch_checkpoint_id = "original-leaf"
    await db.commit()
    calls: list[str] = []

    async def tree(
        _saver: None, thread_id: str, *, active_checkpoint_id: str | None
    ) -> MessageTree:
        calls.append(thread_id)
        async with TestSession() as revoked:
            await revoked.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
            )
            await revoked.commit()
        return MessageTree(
            nodes=[], active_tip_message_id=None, active_checkpoint_id="unauthorized-new-leaf"
        )

    monkeypatch.setattr(worker, "async_session", TestSession)
    monkeypatch.setattr(worker, "get_checkpointer", lambda: None)
    monkeypatch.setattr(worker.thread_branch_service, "build_message_tree", tree)
    await worker._activate_latest_branch_leaf_if_needed(
        target=WorkerConversationTarget(conversation.id, own),
        moldy_source="regenerate",
        final_status="completed",
    )
    stored = await db.scalar(
        select(Conversation.active_branch_checkpoint_id).where(Conversation.id == conversation.id)
    )
    assert calls == [str(conversation.id)]
    assert stored == "original-leaf"
