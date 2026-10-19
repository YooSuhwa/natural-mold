"""Fresh F2 bounded runtime actor and persisted capability controls for candidate R12."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import agent_invocation_service, conversation_stream_service
from app.services.chat.runtime_context import get_agent_with_tools
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def _prepare_owned_agent(
    db: AsyncSession, case: CollectorCase, scopes: PrivateScopes
) -> Agent:
    credential = await credential_service.create(
        db,
        CredentialCreation("openai", "F2 owned dummy", {"api_key": "F2_DUMMY_ONLY"}),
        scopes.first,
    )
    agent = await db.get(Agent, case.conversation.agent_id)
    assert agent is not None
    agent.llm_credential_id = credential.id
    await db.execute(update(Tool).values(credential_id=credential.id))
    await db.commit()
    await ensure_conversation_runtime_policy(
        db, ConversationPolicyTarget(case.conversation.id, scopes.first)
    )
    await db.commit()
    agent_id = agent.id
    db.expunge_all()
    loaded = await get_agent_with_tools(db, agent_id, scopes.first)
    assert loaded is not None
    return loaded


@pytest.mark.parametrize("revoked", [False, True])
async def test_ordinary_chat_rechecks_actor_before_decryption(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes, revoked: bool
) -> None:
    await _prepare_owned_agent(db, collector_case, private_scopes)
    scope = private_scopes.first
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == scope.org_id,
                OrganizationMember.user_id == scope.user_id,
            )
            .values(status="suspended")
        )
        await db.commit()
        with pytest.raises(AppError) as oracle:
            await resolve_private_actor(
                db, PrivateResourceActor(scope.user_id, scope.org_id, scope.tenant_id)
            )
        assert oracle.value.status == 403
    user = CurrentUser(id=scope.user_id, email="fixture@example.com", name="Fixture")
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    denied = False
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        patches.setattr(
            conversation_stream_service,
            "resolve_llm_api_key_for_agent",
            resolve_llm_api_key_for_agent,
        )
        try:
            await conversation_stream_service.resolve_agent_context(
                db, collector_case.conversation.id, user, context=scope, checkpoint_id=None
            )
        except AppError:
            denied = True
    assert denied == revoked
    assert decrypt.await_count == (0 if revoked else 2)


@pytest.mark.parametrize("foreign_edge", [False, True])
async def test_runtime_reload_rechecks_cached_tool_parent_before_decryption(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    foreign_edge: bool,
) -> None:
    cached = await _prepare_owned_agent(db, collector_case, private_scopes)
    tool = cached.tool_links[0].tool
    assert tool is not None
    owned_credential_id = tool.credential_id
    if foreign_edge:
        foreign = await credential_service.create(
            db,
            CredentialCreation("openai", "F2 foreign dummy", {"api_key": "F2_DUMMY_FOREIGN"}),
            private_scopes.second,
        )
        await db.execute(
            update(Tool)
            .where(Tool.id == tool.id)
            .values(credential_id=foreign.id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert tool.credential_id == owned_credential_id
        persisted_id = await db.scalar(select(Tool.credential_id).where(Tool.id == tool.id))
        assert persisted_id == foreign.id
    reloaded = await get_agent_with_tools(db, cached.id, private_scopes.first)
    assert reloaded is not None
    user = CurrentUser(id=private_scopes.first.user_id, email="fixture@example.com", name="Fixture")
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    denied = False
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        patches.setattr(
            agent_invocation_service, "resolve_llm_api_key_for_agent", resolve_llm_api_key_for_agent
        )
        try:
            await agent_invocation_service.build_agent_config_for_loaded_agent(
                db,
                reloaded,
                context=private_scopes.first,
                thread_id=str(collector_case.conversation.id),
                principal=agent_invocation_service.AgentInvocationPrincipal.chat_user(user),
                source="chat",
            )
        except AppError:
            denied = True
    assert denied == foreign_edge
    assert decrypt.await_count == (0 if foreign_edge else 2)
