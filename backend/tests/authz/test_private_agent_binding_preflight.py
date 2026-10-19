"""Agent capability decisions precede implicit and explicit private SQL writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import agent_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("kind", ["skill", "mcp", "subagent", "mixed"])
@pytest.mark.parametrize("missing", [False, True])
async def test_agent_binding_denial_preserves_pending_sql(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    operation: Literal["create", "update"],
    kind: Literal["skill", "mcp", "subagent", "mixed"],
    missing: bool,
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    parent = await agent_service.get_agent(db, api_case.first.agent_id, own)
    pending = await db.get(Agent, api_case.second.agent_id)
    assert parent is not None and pending is not None
    own_skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Owned binding",
        slug="owned-binding",
        kind="text",
    )
    skill = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign binding",
        slug="foreign-binding",
        kind="text",
    )
    server = McpServer(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign binding",
        transport="streamable_http",
        url="https://invalid.local",
    )
    db.add_all([own_skill, skill, server])
    await db.flush()
    tool = McpTool(server_id=server.id, name="foreign_binding", enabled=True)
    db.add(tool)
    await db.commit()
    skill_ids = []
    mcp_ids = []
    sub_ids = []
    match kind:
        case "skill":
            skill_ids = [uuid4() if missing else skill.id]
        case "mcp":
            mcp_ids = [uuid4() if missing else tool.id]
        case "subagent":
            sub_ids = [uuid4() if missing else pending.id]
        case "mixed":
            skill_ids = [own_skill.id]
            sub_ids = [uuid4() if missing else pending.id]
        case unreachable:
            assert_never(unreachable)
    original = pending.name
    pending.name = "Pending unrelated binding content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        match operation:
            case "create":
                await agent_service.create_agent(
                    db,
                    AgentCreate(
                        name="Denied binding",
                        system_prompt="Fixture",
                        model_id=parent.model_id,
                        skill_ids=skill_ids,
                        mcp_tool_ids=mcp_ids,
                        sub_agent_ids=sub_ids,
                    ),
                    own,
                )
            case "update":
                await agent_service.update_agent(
                    db,
                    parent,
                    AgentUpdate(skill_ids=skill_ids, mcp_tool_ids=mcp_ids, sub_agent_ids=sub_ids),
                    context=own,
                )
            case unreachable:
                assert_never(unreachable)
    except HTTPException as denied:
        assert denied.status_code == 400
    else:
        pytest.fail("Expected private binding denial")
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == pending.id))
    staged = pending in db.dirty
    await db.rollback()
    assert flush.call_count == 0
    assert stored == original
    assert staged
