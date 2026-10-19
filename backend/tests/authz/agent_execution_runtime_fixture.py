"""Shared durable-run fixtures for runtime ownership tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    ensure_unit_scope,
    unit_context,
)
from tests.conftest import TEST_USER_ID, TestSession


async def seed_runtime_run(
    *, archived: bool = False
) -> tuple[ConversationRun, Agent, Conversation]:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="runtime-owner@test.com", name="Runtime Owner")
        db.add(user)
        model = Model(provider="openai", model_name="runtime", display_name="Runtime")
        db.add(model)
        await db.flush()
        await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
        agent = Agent(
            user_id=user.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name="Runtime Agent",
            system_prompt="run",
            model_id=model.id,
            archived_at=datetime.now(UTC) if archived else None,
            archived_by=user.id if archived else None,
        )
        db.add(agent)
        await db.flush()
        conversation = Conversation(
            agent_id=agent.id,
            user_id=user.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_name_snapshot=agent.name,
            title="Runtime",
            last_activity_at=datetime.now(UTC),
        )
        db.add(conversation)
        await db.flush()
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=agent.id,
            context=unit_context(user.id),
            source="start",
            input_preview="runtime",
        )
        await db.commit()
        return run, agent, conversation


def runtime_request(
    run: ConversationRun, agent: Agent, conversation: Conversation
) -> RuntimeOwnershipRequest:
    return RuntimeOwnershipRequest(
        run_id=run.id,
        conversation_id=conversation.id,
        root_agent_id=agent.id,
        context=OrgContext(
            org_id=agent.org_id,
            tenant_id=agent.tenant_id,
            user_id=agent.user_id,
        ),
    )
