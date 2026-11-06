"""One ``mcp.tool_call`` audit event per MCP tool call decision (app/mcp/decisions.py)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from langchain_core.tools import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.mcp_auth_errors import McpAuthError
from app.agent_runtime.mcp_cache import MCPToolWithRetry
from app.mcp import decisions
from app.mcp.sdk import GatewayRefused
from app.models.audit_event import AuditEvent
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, seed_agent

CONFIG = {
    "tenant_id": str(TEST_TENANT_ID),
    "org_id": str(TEST_ORG_ID),
    "user_id": str(TEST_USER_ID),
    "mcp_server_id": "11111111-1111-1111-1111-111111111111",
    "mcp_tool_id": "22222222-2222-2222-2222-222222222222",
    "mcp_tool_name": "send",
    "conversation_id": "33333333-3333-3333-3333-333333333333",
    "mcp_reviewed_fingerprint": "f" * 64,
}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_agent(db)
    await db.commit()


def _tool(behaviour: Any, calls: list[dict[str, Any]]) -> MCPToolWithRetry:
    async def send(**arguments: Any) -> str:
        calls.append(arguments)
        if isinstance(behaviour, BaseException):
            raise behaviour
        return "sent"

    schema = {
        "type": "object",
        "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
    }
    original = StructuredTool(name="send", description="send", args_schema=schema, coroutine=send)
    return MCPToolWithRetry(
        original,
        max_retries=2,
        retry_delay=0,
        decision_context=decisions.DecisionContext.from_config(
            CONFIG, risk_level="write", gateway=True
        ),
    )


async def _events() -> list[AuditEvent]:
    async with TestSession() as check:
        return list(
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "mcp.tool_call"))
        )


def _refused(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mail.example.com/mcp")
    return httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(status, request=request)
    )


async def test_a_successful_call_records_who_what_and_the_reviewed_version() -> None:
    calls: list[dict[str, Any]] = []
    assert await _tool("ok", calls).ainvoke({"to": "boss@example.com", "body": "secret"}) == "sent"

    [event] = await _events()
    assert (event.outcome, event.reason_code, event.actor_user_id) == (
        "success",
        None,
        TEST_USER_ID,
    )
    assert (event.target_type, event.target_id, event.target_name_snapshot) == (
        "mcp_tool",
        CONFIG["mcp_tool_id"],
        "send",
    )
    assert event.event_metadata is not None
    assert event.event_metadata["reviewed_fingerprint"] == "f" * 64
    assert event.event_metadata["risk_level"] == "write"
    assert event.event_metadata["gateway"] is True
    assert event.event_metadata["argument_names"] == ["body", "to"]
    assert "boss@example.com" not in str(event.event_metadata)  # names, never values


async def test_a_gateway_refusal_is_denied_and_never_retried() -> None:
    calls: list[dict[str, Any]] = []
    result = await _tool(GatewayRefused("tool 'send' is not allowed for this run"), calls).ainvoke(
        {"to": "x"}
    )

    assert "not allowed for this run" in str(result)
    assert len(calls) == 1
    [event] = await _events()
    assert (event.outcome, event.reason_code) == ("denied", "mcp_gateway_refused")


async def test_a_failing_server_records_a_failure_after_its_retries() -> None:
    calls: list[dict[str, Any]] = []
    await _tool(ConnectionError("down"), calls).ainvoke({"to": "x"})

    assert len(calls) == 2
    [event] = await _events()
    assert (event.outcome, event.reason_code) == ("failure", "mcp_error:ConnectionError")
    assert event.event_metadata is not None and event.event_metadata["attempts"] == 2


async def test_a_refused_credential_is_denied() -> None:
    calls: list[dict[str, Any]] = []
    with pytest.raises(McpAuthError):
        await _tool(_refused(401), calls)._arun(to="x")

    [event] = await _events()
    assert (event.outcome, event.reason_code) == ("denied", "mcp_auth_required")


async def test_a_record_that_cannot_be_written_never_fails_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_ctx: Any) -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(decisions, "tenant_session", broken)
    assert await _tool("ok", []).ainvoke({"to": "x"}) == "sent"


def test_a_config_without_a_company_records_nothing() -> None:
    assert decisions.DecisionContext.from_config({}, risk_level=None, gateway=False) is None


async def test_the_default_and_the_lease_path_record_the_same_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import mcp_cache, mcp_tool_loader
    from app.agent_runtime.mcp_strict_loader import load_selected_mcp_tools_strict

    calls: list[dict[str, Any]] = []
    config = {**CONFIG, "name": "send", "mcp_server_url": "https://mail.example.com/mcp"}

    async def load(*_args: Any) -> list[Any]:
        return [_tool("ok", calls)._original_tool]  # noqa: SLF001 - the bare MCP tool

    async def discover(*_args: Any) -> list[Any]:
        return await load()

    monkeypatch.setattr(mcp_tool_loader, "load_server_tools", load)
    await mcp_cache.clear_mcp_tool_cache()
    [default] = await mcp_tool_loader._build_mcp_tools([dict(config)])  # noqa: SLF001
    [strict] = await load_selected_mcp_tools_strict(
        [dict(config)], discover=discover, call_timeout_seconds=5
    )

    assert default._decision_context == strict.tool._decision_context  # type: ignore[attr-defined]  # noqa: SLF001
    assert default._decision_context is not None  # type: ignore[attr-defined]  # noqa: SLF001
