"""Share roots retain private scope, including public token parent-chain checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.share_link import ShareLink
from app.schemas.agent import AgentCreate
from app.services import agent_service, share_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_share_writer_lookup_and_revoke_use_conversation_scope(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    link = await share_service.create_or_get_active_share(
        db, collector_case.conversation, private_scopes.first
    )
    assert (link.created_by, link.org_id, link.tenant_id) == (
        private_scopes.first.user_id,
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )
    assert link.user_id == link.created_by
    assert (
        await share_service.get_active_share_for_conversation(
            db, collector_case.conversation.id, private_scopes.second
        )
        is None
    )
    assert (
        await share_service.revoke_share(db, collector_case.conversation.id, private_scopes.second)
        == []
    )
    assert await share_service.get_share_by_token(db, link.share_token) is not None


async def test_public_token_rejects_valid_but_foreign_parent_scope(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    link = await share_service.create_or_get_active_share(
        db, collector_case.conversation, private_scopes.first
    )
    assert (
        await share_service.load_share_with_conversation_and_agent(db, link.share_token) is not None
    )
    await db.execute(
        update(ShareLink)
        .where(ShareLink.id == link.id)
        .values(org_id=private_scopes.second.org_id, tenant_id=private_scopes.second.tenant_id)
    )
    await db.commit()
    assert await share_service.load_share_with_conversation_and_agent(db, link.share_token) is None


async def test_public_share_denies_same_user_other_company_agent_edge(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    conversation = collector_case.conversation
    owned_agent = await db.get(Agent, conversation.agent_id)
    assert owned_agent is not None
    foreign = await agent_service.create_agent(
        db,
        AgentCreate(name="Foreign company", system_prompt="Dummy", model_id=owned_agent.model_id),
        private_scopes.second,
    )
    link = await share_service.create_or_get_active_share(db, conversation, private_scopes.first)
    assert await share_service.load_share_with_conversation_and_agent(db, link.share_token)
    conversation.agent_id = foreign.id
    await db.commit()
    db.expunge_all()
    assert await share_service.load_share_with_conversation_and_agent(db, link.share_token) is None
