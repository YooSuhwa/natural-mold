"""Checkpoint metrics and attachment hydration share the captured actor boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.authz.worker_conversation_scope import WorkerConversationTarget
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.services import chat_service
from app.services import conversation_run_worker as worker
from app.services.chat.conversations import create_conversation
from app.services.thread_branch_service import MessageTree
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("surface", ["metrics", "attachments"])
@pytest.mark.parametrize("boundary", ["normal", "revoked", "moved"])
async def test_worker_checkpoint_siblings_reject_revoked_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    surface: Literal["metrics", "attachments"],
    boundary: Literal["normal", "revoked", "moved"],
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    cfg = AgentConfig(
        provider="openai",
        model_name="fixture",
        api_key=None,
        base_url=None,
        system_prompt="Fixture",
        tools_config=[],
        thread_id=str(conversation.id),
        agent_id=str(api_case.first.agent_id),
        user_id=str(own.user_id),
        org_context=own,
    )
    await db.commit()
    match boundary:
        case "normal":
            pass
        case "revoked":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
            )
            await db.commit()
        case "moved":
            other = private_scopes.second
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(org_id=other.org_id, tenant_id=other.tenant_id)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            assert conversation.org_id == own.org_id
        case unreachable:
            assert_never(unreachable)
    calls: list[str] = []

    async def tree(
        _saver: None, thread_id: str, *, active_checkpoint_id: str | None
    ) -> MessageTree:
        calls.append(thread_id)
        return MessageTree(nodes=[], active_tip_message_id=None, active_checkpoint_id=None)

    async def turn(_db: AsyncSession, _target: Conversation) -> None:
        calls.append(str(conversation.id))
        return

    monkeypatch.setattr(worker, "async_session", TestSession)
    monkeypatch.setattr(worker, "get_checkpointer", lambda: None)
    monkeypatch.setattr(worker.thread_branch_service, "build_message_tree", tree)
    monkeypatch.setattr(chat_service, "resolve_turn_user_message_id", turn)
    match surface:
        case "metrics":
            await worker._prepare_run_metrics(
                conversation_id=conversation.id, cfg=cfg, started_at=1.0
            )
        case "attachments":
            await worker._backfill_turn_attachments(
                WorkerConversationTarget(conversation.id, own), []
            )
        case unreachable:
            assert_never(unreachable)
    assert len(calls) == (1 if boundary == "normal" else 0)
