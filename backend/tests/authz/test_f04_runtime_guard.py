"""F-04: until F-05, a ``per_user`` resource never runs on its owner's ``credential_id``."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.per_user_guard import CREDENTIAL_BINDING_REQUIRED
from app.exceptions import ValidationError
from app.mcp.apps_binding_lifecycle import McpAppsError
from app.models.mcp_tool import AgentMcpToolLink
from app.models.tool import AgentToolLink
from app.services import chat_service, mcp_apps_service
from app.tools.execution_scope import ToolRunOptions, ToolRunTarget
from app.tools.registry import registry as tool_registry
from app.tools.runner import run_tool
from tests.authz.d04_support import add_server
from tests.authz.e01_support import add_agent, add_credential, add_tool, seed
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _config(db: AsyncSession, agent_id: Any) -> list[dict[str, Any]]:
    context = unit_context(TEST_USER_ID)
    fetched = await chat_service.get_agent_with_tools(db, agent_id, context)
    assert fetched is not None
    return await chat_service.build_tools_config(fetched, db=db, context=context)


async def test_a_per_user_tool_is_refused_in_the_run_config(db: AsyncSession) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    tool.credential_id = (await add_credential(db)).id
    tool.credential_mode = "per_user"
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    with pytest.raises(ValidationError) as caught:
        await _config(db, agent.id)
    assert caught.value.code == CREDENTIAL_BINDING_REQUIRED


async def test_a_per_user_mcp_server_is_refused_in_the_run_config(db: AsyncSession) -> None:
    agent = await add_agent(db)
    server, mcp_tool = await add_server(db)
    server.credential_id = (await add_credential(db, definition_key="mcp_oauth2")).id
    server.credential_mode = "per_user"
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    with pytest.raises(ValidationError) as caught:
        await _config(db, agent.id)
    assert caught.value.code == CREDENTIAL_BINDING_REQUIRED


async def test_a_shared_tool_still_builds(db: AsyncSession) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    [entry] = await _config(db, agent.id)
    assert entry["tool_id"] == str(tool.id)


async def test_a_per_user_tool_test_run_is_refused(db: AsyncSession) -> None:
    tool = await add_tool(db)
    tool.credential_id = (await add_credential(db)).id
    tool.credential_mode = "per_user"
    await db.commit()
    result = await run_tool(
        db,
        ToolRunTarget(tool.id, unit_context(TEST_USER_ID)),
        ToolRunOptions(registry=tool_registry),
    )
    assert not result.success
    assert (result.error or "").startswith(CREDENTIAL_BINDING_REQUIRED)


async def test_mcp_apps_calls_refuse_a_per_user_server(db: AsyncSession) -> None:
    server, _ = await add_server(db)
    server.credential_mode = "per_user"
    context = cast(Any, SimpleNamespace(server=server))
    with pytest.raises(McpAppsError) as caught:
        await mcp_apps_service._auth_for_context(db, context)  # noqa: SLF001
    assert (caught.value.code, caught.value.status_code) == ("credential_binding_required", 409)
