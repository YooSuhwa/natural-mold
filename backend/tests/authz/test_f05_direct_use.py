"""F-05: direct uses (tool test run, MCP Apps call) resolve the caller's own connection."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.apps_binding_lifecycle import McpAppsError
from app.services import mcp_apps_service
from app.tools.execution_scope import ToolRunOptions, ToolRunTarget
from app.tools.registry import registry as tool_registry
from app.tools.runner import _load_credential, run_tool
from tests.authz.d04_support import add_server, add_tool
from tests.authz.e01_support import seed
from tests.authz.f05_support import CALLER, bind, secret
from tests.authz.unit_scope_fixture import unit_context


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _per_user_tool(db: AsyncSession) -> Any:
    tool = await add_tool(db, owner=CALLER)
    tool.credential_id = (await secret(db, CALLER, "owner-account")).id
    tool.credential_mode, tool.credential_definition_key = "per_user", "http_bearer"
    tool.parameters = {"url": "https://api.example.test"}
    await db.commit()
    return tool


async def test_a_per_user_tool_test_run_needs_the_callers_connection(db: AsyncSession) -> None:
    tool = await _per_user_tool(db)
    result = await run_tool(
        db, ToolRunTarget(tool.id, unit_context(CALLER)), ToolRunOptions(registry=tool_registry)
    )
    assert not result.success
    assert (result.error or "").startswith("credential_binding_required")


async def test_a_per_user_tool_test_run_uses_the_callers_connection(db: AsyncSession) -> None:
    tool = await _per_user_tool(db)
    await bind(db, CALLER, "tool", tool.id, await secret(db, CALLER, "my-connection"))
    resolved = await _load_credential(db, tool, unit_context(CALLER))
    payload = resolved.values.reveal() if resolved else None
    assert payload is not None and payload["token"] == "my-connection"


def _apps_context(server: Any) -> Any:
    binding = SimpleNamespace(credential_subject_user_id=CALLER)
    return cast(
        Any, SimpleNamespace(server=server, binding=binding, org_context=unit_context(CALLER))
    )


async def test_mcp_apps_calls_need_a_per_user_connection(db: AsyncSession) -> None:
    server, _ = await add_server(db, owner=CALLER)
    server.credential_mode, server.credential_definition_key = "per_user", "http_bearer"
    await db.commit()
    with pytest.raises(McpAppsError) as caught:
        await mcp_apps_service._auth_for_context(db, _apps_context(server))  # noqa: SLF001
    assert (caught.value.code, caught.value.status_code) == ("credential_binding_required", 409)
    await bind(db, CALLER, "mcp_server", server.id, await secret(db, CALLER, "app-token"))
    auth = await mcp_apps_service._auth_for_context(db, _apps_context(server))  # noqa: SLF001
    assert auth.credentials is not None and auth.credentials["token"] == "app-token"
