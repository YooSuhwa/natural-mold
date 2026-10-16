"""Actual Assistant callbacks recheck current actor and child presentation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import event, update
from sqlalchemy.engine import Connection
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext, _DBAPIAnyExecuteParams
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.assistant_agent import build_assistant_agent
from app.agent_runtime.assistant.tools import read_tools
from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember, Tenant
from app.models.tool import Tool
from app.models.user import User
from tests.authz.assistant_projection_fixture import (
    assistant_sessions as assistant_sessions,
)
from tests.authz.assistant_projection_fixture import (
    binding_target as binding_target,
)
from tests.authz.assistant_projection_fixture import create_binding, visible_ids, visible_names

if TYPE_CHECKING:
    from tests.authz.assistant_projection_fixture import BindingTarget
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_scope import AgentCase

pytest_plugins = ["tests.authz.test_private_agent_scope"]
type ActorChange = Literal["membership", "account", "company", "organization"]


async def test_actual_assistant_config_refreshes_moved_cached_children(
    db: AsyncSession, binding_target: BindingTarget, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    child = await create_binding(db, binding_target, own)
    cached = await get_agent_with_eager_load(db, binding_target.agent_id, own)
    assert cached is not None
    assert visible_ids(cached, binding_target.kind) == {child.linked_id}
    tools = read_tools.build_read_tools(db, binding_target.agent_id, own)
    reader = next(tool for tool in tools if tool.name == "get_agent_config")
    normal = await reader.ainvoke({})
    if binding_target.kind != "mcp":
        assert binding_target.marker in normal
    await child.move_to(db, private_scopes.second)
    assert child.resource.org_id == own.org_id
    refreshed = await get_agent_with_eager_load(db, binding_target.agent_id, own)
    assert refreshed is not None and refreshed is cached
    assert visible_ids(refreshed, binding_target.kind) == set()
    response = await reader.ainvoke({})
    assert binding_target.marker not in response
    assert await child.persisted_ids(db) == {child.linked_id}


async def test_assistant_visible_child_metadata_is_current(
    db: AsyncSession, binding_target: BindingTarget, private_scopes: PrivateScopes
) -> None:
    child = await create_binding(db, binding_target, private_scopes.first)
    cached = await get_agent_with_eager_load(db, binding_target.agent_id, private_scopes.first)
    assert cached is not None
    await child.rename_visible(db, "current-marker")
    assert visible_names(cached, binding_target.kind) == {binding_target.marker}
    refreshed = await get_agent_with_eager_load(db, binding_target.agent_id, private_scopes.first)
    assert refreshed is not None and refreshed is cached
    assert visible_names(refreshed, binding_target.kind) == {"current-marker"}


@pytest.mark.parametrize("change", ["membership", "account", "company", "organization"])
async def test_revoked_assistant_loader_rejects_before_target_sql(
    db: AsyncSession, agent_case: AgentCase, private_scopes: PrivateScopes, change: ActorChange
) -> None:
    own = private_scopes.first
    assert await get_agent_with_eager_load(db, agent_case.agent_id, own) is not None
    match change:
        case "membership":
            mutation = (
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
            )
        case "account":
            mutation = update(User).where(User.id == own.user_id).values(is_active=False)
        case "company":
            mutation = update(Tenant).where(Tenant.id == own.tenant_id).values(status="suspended")
        case "organization":
            mutation = (
                update(Organization)
                .where(Organization.id == own.org_id)
                .values(status="archived", archived_at=datetime.now(UTC))
            )
        case unreachable:
            assert_never(unreachable)
    await db.execute(mutation.execution_options(synchronize_session=False))
    await db.commit()
    pending = Tool(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="pending-synthetic",
        definition_key="http_request",
    )
    db.add(pending)
    statements: list[str] = []
    connection = await db.connection()
    engine = connection.engine.sync_engine

    # SQLAlchemy's documented callback signature requires all six arguments.
    def observe(
        _connection: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: _DBAPIAnyExecuteParams,
        _context: ExecutionContext,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        loaded = await get_agent_with_eager_load(db, agent_case.agent_id, own)
        with pytest.raises(AppError) as denied:
            await build_assistant_agent(db, agent_case.agent_id, own, "synthetic-assistant-thread")
        assert denied.value.code == "AGENT_NOT_FOUND"
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert loaded is None
    assert pending in db.new
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert not any(
        "FROM agents" in statement or "FROM tools" in statement for statement in statements
    )
    await db.rollback()
    tools = read_tools.build_read_tools(db, agent_case.agent_id, own)
    reader = next(tool for tool in tools if tool.name == "get_agent_config")
    assert await reader.ainvoke({}) == "에이전트를 찾을 수 없습니다."
