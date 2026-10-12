"""Focused in-memory history fixture for Agent purge service tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import OrganizationRoleGrant
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, seed_agent


@dataclass(frozen=True, slots=True)
class FocusedPurgeHistory:
    agent_id: UUID
    conversation_id: UUID
    run_id: UUID
    saver: InMemorySaver


async def grant_test_org_admin() -> None:
    async with TestSession() as db:
        existing = await db.scalar(
            select(OrganizationRoleGrant.id).where(
                OrganizationRoleGrant.org_id == TEST_ORG_ID,
                OrganizationRoleGrant.tenant_id == TEST_TENANT_ID,
                OrganizationRoleGrant.role == "admin",
                OrganizationRoleGrant.principal_type == "user",
                OrganizationRoleGrant.principal_id == TEST_USER_ID,
            )
        )
        if existing is None:
            db.add(
                OrganizationRoleGrant(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    role="admin",
                    principal_type="user",
                    principal_id=TEST_USER_ID,
                )
            )
            await db.commit()


async def seed_focused_purge_history() -> FocusedPurgeHistory:
    """Create one retained conversation/run and its raw checkpoint."""
    saver = InMemorySaver()
    async with TestSession() as db:
        user, _, agent = await seed_agent(db, name="History purge")
        conversation = Conversation(
            agent_id=agent.id,
            user_id=user.id,
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            agent_name_snapshot=agent.name,
            last_activity_at=datetime.now(UTC),
            title="retained private title",
        )
        db.add(conversation)
        await db.flush()
        run = ConversationRun(
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=user.id,
            source="chat",
            status="completed",
            is_active=False,
        )
        db.add(run)
        await db.flush()
        db.add(
            ConversationRunAgent(
                run_id=run.id,
                agent_id=agent.id,
                agent_id_snapshot=agent.id,
                role="root",
                path=[],
            )
        )
        await db.commit()
        result = FocusedPurgeHistory(agent.id, conversation.id, run.id, saver)
    checkpoint = empty_checkpoint()
    checkpoint["id"] = "purge-checkpoint"
    checkpoint["channel_values"] = {
        "messages": [HumanMessage(id="purge-message", content="retained content")]
    }
    checkpoint["channel_versions"] = {"messages": "1"}
    await saver.aput(
        {"configurable": {"thread_id": str(result.conversation_id), "checkpoint_ns": ""}},
        checkpoint,
        {"source": "input", "step": 0},
        {"messages": "1"},
    )
    return result


__all__ = ["FocusedPurgeHistory", "grant_test_org_admin", "seed_focused_purge_history"]
