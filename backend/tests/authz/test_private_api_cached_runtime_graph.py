"""Trace the real API config producer through the shared cached runtime loader."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.tool import AgentToolLink, Tool
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import agent_invocation_service
from app.services.chat.runtime_context import get_agent_with_tools
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("foreign_edge", [False, True])
async def test_actual_api_config_rejects_changed_cached_tool_edge(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes, foreign_edge: bool
) -> None:
    own = private_scopes.first
    principal = ApiKeyPrincipal(api_case.key, own.user_id, own)
    conversation = await runtime_service.create_api_conversation(
        db, api_case.first, principal=principal
    )
    credential = await credential_service.create(
        db, CredentialCreation("openai", "F2 owned dummy", {"api_key": "F2_DUMMY_ONLY"}), own
    )
    agent = await db.get(Agent, api_case.first.agent_id)
    assert agent is not None
    agent.llm_credential_id = credential.id
    tool = Tool(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="F2 tool",
        definition_key="http_request",
        credential_id=credential.id,
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await ensure_conversation_runtime_policy(db, ConversationPolicyTarget(conversation.id, own))
    await db.commit()
    agent_id, tool_id = agent.id, tool.id
    db.expunge_all()
    cached = await get_agent_with_tools(db, agent_id, own)
    assert cached is not None
    cached_tool = cached.tool_links[0].tool
    assert cached_tool is not None
    if foreign_edge:
        foreign = await credential_service.create(
            db,
            CredentialCreation("openai", "F2 foreign dummy", {"api_key": "F2_DUMMY_FOREIGN"}),
            private_scopes.second,
        )
        await db.execute(
            update(Tool)
            .where(Tool.id == tool_id)
            .values(credential_id=foreign.id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert cached_tool.credential_id == credential.id
        assert await db.scalar(select(Tool.credential_id).where(Tool.id == tool_id)) == foreign.id
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    denied = False
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        patches.setattr(
            agent_invocation_service, "resolve_llm_api_key_for_agent", resolve_llm_api_key_for_agent
        )
        try:
            await runtime_service.build_config_for_run(
                db,
                principal=principal,
                deployment=api_case.first,
                conversation=conversation,
                external_user=None,
                required_scope="invoke",
            )
        except AppError:
            denied = True
    assert denied == foreign_edge
    assert decrypt.await_count == (0 if foreign_edge else 2)
