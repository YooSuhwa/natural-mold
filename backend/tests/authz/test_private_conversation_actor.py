"""Detached conversation work uses persisted ownership and freshly checked membership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.authz.conversation_scope import load_private_conversation, resolve_conversation_actor
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services.agent_lifecycle import archive_agent
from app.services.chat.conversations import get_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


async def test_service_reader_does_not_return_other_company_history(
    db: AsyncSession, private_scopes: PrivateScopes, retained_conversation: Conversation
) -> None:
    # Given a retained company-A conversation and the same user's company-B scope.
    assert private_scopes.second.org_id != retained_conversation.org_id
    # When the compatibility service reader is called for that foreign ID.
    row = await get_conversation(db, retained_conversation.id, private_scopes.second)
    # Then private content remains absent outside its declared organization.
    assert row is None


@pytest.fixture
async def retained_conversation(db: AsyncSession, private_scopes: PrivateScopes) -> Conversation:
    scope = private_scopes.first
    conversation = Conversation(
        user_id=scope.user_id,
        org_id=scope.org_id,
        tenant_id=scope.tenant_id,
        agent_id=None,
        agent_name_snapshot="Archived fixture agent",
        agent_lifecycle_state="purged",
        agent_purged_at=datetime.now(UTC),
        last_activity_at=datetime.now(UTC),
        title="Retained fixture history",
    )
    db.add(conversation)
    await db.commit()
    return conversation


async def test_detached_actor_uses_target_org_after_user_switches_company(
    db: AsyncSession, private_scopes: PrivateScopes, retained_conversation: Conversation
) -> None:
    await db.execute(
        update(User)
        .where(User.id == private_scopes.first.user_id)
        .values(last_active_org_id=private_scopes.second.org_id)
    )
    await db.commit()
    context = await resolve_conversation_actor(
        db, retained_conversation.id, private_scopes.first.user_id
    )
    assert context.org_id == private_scopes.first.org_id
    assert context.tenant_id == private_scopes.first.tenant_id
    assert await load_private_conversation(db, retained_conversation.id, context) is not None
    assert (
        await load_private_conversation(db, retained_conversation.id, private_scopes.second) is None
    )


async def test_detached_actor_rejects_removed_membership(
    db: AsyncSession, private_scopes: PrivateScopes, retained_conversation: Conversation
) -> None:
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == private_scopes.first.org_id,
            OrganizationMember.user_id == private_scopes.first.user_id,
        )
    )
    await db.commit()
    with pytest.raises(AppError):
        await resolve_conversation_actor(db, retained_conversation.id, private_scopes.first.user_id)


async def test_detached_actor_rejects_foreign_owner_without_enumeration(
    db: AsyncSession, retained_conversation: Conversation
) -> None:
    with pytest.raises(AppError) as captured:
        await resolve_conversation_actor(db, retained_conversation.id, uuid4())
    assert captured.value.status == 404


async def test_agent_delete_preserves_owned_history_and_name(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    from app.models.agent import Agent
    from app.models.model import Model
    from app.services.chat.conversations import create_conversation

    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    agent = Agent(
        org_id=private_scopes.first.org_id,
        tenant_id=private_scopes.first.tenant_id,
        user_id=private_scopes.first.user_id,
        name="Original fixture name",
        system_prompt="Fixture",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    agent_id = agent.id
    conversation = await create_conversation(db, agent.id, context=private_scopes.first)
    conversation_id = conversation.id
    await db.commit()
    actor = CurrentUser(
        id=private_scopes.first.user_id,
        email="owner@example.com",
        name="Owner",
    )
    await archive_agent(db, actor, agent_id)
    await db.commit()
    db.expire_all()
    preserved_agent = await db.get(Agent, agent_id)
    assert preserved_agent is not None
    assert preserved_agent.archived_at is not None
    preserved = await load_private_conversation(db, conversation_id, private_scopes.first)
    assert preserved is not None
    assert preserved.agent_id == agent_id
    assert preserved.agent_lifecycle_state == "archived"
    assert preserved.agent_name_snapshot == "Original fixture name"


async def test_retained_history_read_is_scoped_to_current_company(
    raw_client: AsyncClient, private_scopes: PrivateScopes, retained_conversation: Conversation
) -> None:
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.first.org_id))
    own = await raw_client.get(f"/api/conversations/{retained_conversation.id}")
    assert own.status_code == 200, own.text
    assert own.json()["agent_id"] is None
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.second.org_id))
    foreign = await raw_client.get(f"/api/conversations/{retained_conversation.id}")
    assert foreign.status_code == 404, foreign.text
