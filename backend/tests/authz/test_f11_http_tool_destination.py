"""F-11 done-when: a credential is never sent outside its allowed hosts (plan 11.9, D18).

Covers the generic HTTP tool on both paths (tool test run and chat runtime), the run
identity rule for empty allowed hosts, and user-supplied auth headers.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.tool_factory import create_tool_for_runtime
from app.authz.runtime import legacy_service
from app.config import settings
from app.models.tool import AgentToolLink, Tool
from app.outbound.client import outbound_http_client
from app.outbound.destination import OutboundBlockedError
from app.tools.execution_scope import ToolRunOptions, ToolRunTarget
from app.tools.registry import registry as tool_registry
from app.tools.runner import run_tool
from tests.authz.e01_support import add_agent, seed
from tests.authz.f05_support import identity, loaded, secret
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID

SECRET = "supersecret-token"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


class Wire:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json={"ok": True})

        return outbound_http_client(transport=httpx.MockTransport(handler))

    def leaked(self) -> bool:
        return any(SECRET in r.headers.get("authorization", "") for r in self.requests)


async def _bearer_tool(
    db: AsyncSession, url: str, *, allowed: list[str] | None, headers: Any = None
) -> Tool:
    credential = await secret(db, TEST_USER_ID, SECRET)
    credential.allowed_hosts = allowed
    tool = Tool(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        definition_key="http_request",
        name="api",
        parameters={
            "method": "GET",
            "url": url,
            "headers": headers or {},
            "_credential_definition_key": "http_bearer",
        },
        credential_id=credential.id,
    )
    db.add(tool)
    await db.commit()
    return tool


async def _test_run(db: AsyncSession, tool: Tool, wire: Wire) -> Any:
    async with wire.client() as client:
        return await run_tool(
            db,
            ToolRunTarget(tool.id, unit_context(TEST_USER_ID)),
            ToolRunOptions(registry=tool_registry, http_client=client),
        )


async def test_a_tool_run_never_sends_the_credential_outside_allowed_hosts(
    db: AsyncSession,
) -> None:
    tool = await _bearer_tool(db, "https://evil.example.net/x", allowed=["api.example.com"])
    wire = Wire()
    result = await _test_run(db, tool, wire)
    assert not result.success
    assert "allowed hosts" in (result.error or "")
    assert wire.requests == []


async def test_a_tool_run_to_an_allowed_host_carries_the_credential(db: AsyncSession) -> None:
    tool = await _bearer_tool(db, "https://api.example.com/v1", allowed=["api.example.com"])
    wire = Wire()
    result = await _test_run(db, tool, wire)
    assert result.success, result.error
    assert wire.leaked()  # the bearer reached the allowed host


async def test_a_credentialed_http_tool_refuses_http(db: AsyncSession) -> None:
    tool = await _bearer_tool(db, "http://api.example.com/v1", allowed=["api.example.com"])
    wire = Wire()
    result = await _test_run(db, tool, wire)
    assert not result.success and "https" in (result.error or "")
    assert wire.requests == []


async def test_user_supplied_auth_headers_are_refused_with_a_credential(db: AsyncSession) -> None:
    tool = await _bearer_tool(
        db,
        "https://api.example.com/v1",
        allowed=["api.example.com"],
        headers={"authorization": "Bearer attacker"},
    )
    wire = Wire()
    result = await _test_run(db, tool, wire)
    assert not result.success and "not allowed when a credential" in (result.error or "")
    assert wire.requests == []


async def _runtime_config(db: AsyncSession, tool: Tool, ident: Any) -> dict[str, Any]:
    agent = await add_agent(db, owner=TEST_USER_ID)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    from app.services import chat_service

    [config] = await chat_service.build_tools_config(
        await loaded(db, agent),
        context=unit_context(TEST_USER_ID),
        db=db,
        identity=ident(agent) if ident else None,
        authz=legacy_service(),
    )
    return config


async def _invoke(config: dict[str, Any], wire: Wire, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.agent_runtime import tool_factory

    client = wire.client()
    monkeypatch.setattr(tool_factory, "get_tool_http_client", lambda: client)
    tool = create_tool_for_runtime(config)
    assert tool is not None
    return await tool.ainvoke({})


async def test_runtime_blocks_hosts_outside_allowed_hosts(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = await _bearer_tool(db, "https://evil.example.net/x", allowed=["api.example.com"])
    config = await _runtime_config(db, tool, identity)
    assert config["credential_destination"] == {
        "allowed_hosts": ["api.example.com"],
        "empty_ok": True,
    }
    wire = Wire()
    with pytest.raises(OutboundBlockedError):
        await _invoke(config, wire, monkeypatch)
    assert wire.requests == []


async def test_empty_allowed_hosts_work_only_in_the_owners_own_chat(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "credential_empty_hosts_policy", "enforce")
    tool = await _bearer_tool(db, "https://api.example.com/v1", allowed=None)
    own = await _runtime_config(db, tool, lambda agent: identity(agent, TEST_USER_ID))
    assert own["credential_destination"]["empty_ok"] is True
    wire = Wire()
    await _invoke(own, wire, monkeypatch)
    assert wire.leaked()

    unattended = {**own, "credential_destination": {**own["credential_destination"]}}
    unattended["credential_destination"]["empty_ok"] = False  # trigger/API/shared run shape
    wire = Wire()
    with pytest.raises(OutboundBlockedError, match="no allowed hosts"):
        await _invoke(unattended, wire, monkeypatch)
    assert wire.requests == []


async def test_a_trigger_style_run_marks_empty_hosts_as_not_usable(db: AsyncSession) -> None:
    tool = await _bearer_tool(db, "https://api.example.com/v1", allowed=None)
    config = await _runtime_config(db, tool, None)  # no identity: trigger-style owner run
    assert config["credential_destination"] == {"allowed_hosts": [], "empty_ok": False}


async def test_runtime_fails_closed_without_a_destination(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = await _bearer_tool(db, "https://api.example.com/v1", allowed=["api.example.com"])
    config = await _runtime_config(db, tool, identity)
    config.pop("credential_destination")
    wire = Wire()
    with pytest.raises(OutboundBlockedError, match="destination missing"):
        await _invoke(config, wire, monkeypatch)
    assert wire.requests == []
