from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID


async def _seed_execution_tree(db: AsyncSession) -> tuple[Agent, Agent, Conversation]:
    user = User(id=TEST_USER_ID, email="owner@task6.test", name="Task 6 Owner")
    db.add(user)
    model = Model(provider="openai", model_name="task6-model", display_name="Task 6")
    db.add(model)
    await db.flush()
    await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
    root = Agent(
        user_id=user.id,
        name="Root",
        system_prompt="root",
        model_id=model.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    child = Agent(
        user_id=user.id,
        name="Child",
        system_prompt="child",
        model_id=model.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    db.add_all([root, child])
    await db.flush()
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id, position=0))
    conversation = Conversation(
        agent_id=root.id,
        user_id=user.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=root.name,
        title="Task 6",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    return root, child, conversation


async def _skip_sqlite_advisory_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_op(_db: AsyncSession, _agent_ids: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.agent_lifecycle_locks.acquire_agent_lifecycle_locks",
        no_op,
    )
