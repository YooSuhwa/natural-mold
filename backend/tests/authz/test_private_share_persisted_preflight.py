"""F1 independent persisted-share graph and pre-denial SQL witnesses."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.models.share_link import ShareLink
from app.services import share_service
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


def test_production_provenance() -> None:
    expected = Path(__file__).resolve().parents[2]
    assert Path(share_service.__file__).resolve().is_relative_to(expected)


@pytest.mark.parametrize("edge", ["share", "agent"])
async def test_public_share_rechecks_persisted_graph_after_cached_control(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    edge: Literal["share", "agent"],
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    link = await share_service.create_or_get_active_share(db, conversation, own)
    assert await share_service.load_share_with_conversation_and_agent(db, link.share_token)
    if edge == "share":
        await db.execute(
            update(ShareLink)
            .where(ShareLink.id == link.id)
            .values(org_id=private_scopes.second.org_id, tenant_id=private_scopes.second.tenant_id)
            .execution_options(synchronize_session=False)
        )
    else:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(agent_id=api_case.second.agent_id)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    rejected = await share_service.load_share_with_conversation_and_agent(db, link.share_token)
    # A fresh session must agree with the same persisted capability in a cached session.
    db.expunge_all()
    fresh = await share_service.load_share_with_conversation_and_agent(db, link.share_token)
    assert fresh is None
    assert rejected is None, f"Cached {edge} graph accepted persisted foreign edge"


async def test_public_share_revoked_actor_denies_before_staged_private_insert(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    link = await share_service.create_or_get_active_share(db, conversation, own)
    source = await db.get(Agent, api_case.first.agent_id)
    assert source is not None
    model_id = source.model_id
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id)
        .values(status="removed")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    staged = Agent(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Private pending share preflight",
        system_prompt="Never flushed on denied capability",
        model_id=model_id,
        runtime_name="share-pending-" + uuid4().hex[:16],
    )
    db.add(staged)
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        rejected = await share_service.load_share_with_conversation_and_agent(db, link.share_token)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.id == staged.id)
        )
    await db.rollback()
    assert rejected is None
    assert flush.call_count == 0, (
        f"Denied public share flushed private content {flush.call_count} time(s)"
    )
    assert stored == 0


async def test_private_share_lookup_checks_conversation_parent(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    foreign = await create_conversation(db, api_case.second.agent_id, context=private_scopes.second)
    link = await share_service.create_or_get_active_share(db, conversation, own)
    assert await share_service.get_active_share_for_conversation(db, conversation.id, own)
    await db.execute(
        update(ShareLink)
        .where(ShareLink.id == link.id)
        .values(conversation_id=foreign.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    db.expunge_all()
    rejected = await share_service.get_active_share_for_conversation(db, foreign.id, own)
    assert rejected is None, "Scoped ShareLink root returned its foreign Conversation parent token"
