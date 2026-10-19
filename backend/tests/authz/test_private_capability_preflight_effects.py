"""Remaining capability lookups deny before flushing unrelated pending content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.agent_api.service import utc_now_naive
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.mcp_probe_scope import require_mcp_probe_scope
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("surface", ["probe", "api_refresh", "run_child"])
async def test_capability_lookup_does_not_flush_before_denial(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    surface: Literal["probe", "api_refresh", "run_child"],
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    server = McpServer(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Owned probe fixture",
        transport="streamable_http",
        url="https://invalid.local",
    )
    credential = Credential(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign probe fixture",
        definition_key="openai",
        data_encrypted="dummy",
        key_id="dummy",
        field_keys=[],
    )
    db.add_all([server, credential])
    await db.commit()
    if surface == "api_refresh":
        await db.execute(
            update(AgentApiKey)
            .where(AgentApiKey.id == api_case.key.id)
            .values(revoked_at=utc_now_naive())
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    agent = await db.get(Agent, api_case.first.agent_id)
    assert agent is not None
    original = agent.name
    agent.name = "Pending capability fixture"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    try:
        match surface:
            case "probe":
                await require_mcp_probe_scope(db, server, credential.id)
            case "api_refresh":
                await refresh_api_key_principal(db, ApiKeyPrincipal(api_case.key, own.user_id, own))
            case "run_child":
                target = RunExecutionTarget(
                    uuid4(), RunConversationTarget(conversation.id, api_case.first.agent_id, own)
                )
                denied = await lock_executable_run(db, target) is None
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        assert error.status in (401, 403, 404)
        denied = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == agent.id))
    staged = agent in db.dirty
    await db.rollback()
    assert denied
    assert flush.call_count == 0
    assert stored == original
    assert staged
