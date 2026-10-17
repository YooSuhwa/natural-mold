"""F-12 (plan 11.4, 11.4.1, 11.5): MCP 401/403 are typed and kept apart from transport errors.

Done-when: "401·403과 전송 오류 구분 테스트". The end-to-end cases run the real
langchain-mcp-adapters / MCP SDK streamable-HTTP client against a mocked httpx
transport.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from app.agent_runtime import mcp_auth_context, mcp_cache
from app.agent_runtime.mcp_auth_errors import McpAuthError, classify_mcp_error
from tests.authz.f12_mock_mcp import SERVER_URL, MockMcpServer, mcp_config, status

SCOPE = 'Bearer error="insufficient_scope", scope="write"'
INVALID = 'Bearer error="invalid_token"'
CALL = {"name": "post_message", "args": {"text": "hi"}, "id": "call-1", "type": "tool_call"}


def _status_error(code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", SERVER_URL)
    response = httpx.Response(code, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("refused", request=request, response=response)


def test_classify_separates_401_403_and_transport_errors() -> None:
    expired = _status_error(401, {"WWW-Authenticate": 'Bearer error="invalid_token"'})
    nested = ExceptionGroup("tg", [RuntimeError("x"), _status_error(401)])
    chained = RuntimeError("adapter")
    chained.__cause__ = nested

    auth = classify_mcp_error(chained)
    assert auth is not None and (auth.kind, auth.reason) == ("auth_required", "auth_needed")
    assert classify_mcp_error(expired).reason == "expired"  # type: ignore[union-attr]
    scope = classify_mcp_error(_status_error(403, {"WWW-Authenticate": SCOPE}))
    assert scope is not None and scope.kind == "insufficient_scope"
    assert scope.challenge is not None and scope.challenge.scope == "write"
    denied = classify_mcp_error(_status_error(403))
    assert denied is not None and denied.kind == "permission_denied"
    request = httpx.Request("POST", SERVER_URL)
    for transport in (
        httpx.ConnectError("down", request=request),
        httpx.ReadTimeout("slow", request=request),
        TimeoutError(),
        _status_error(500),
        _status_error(404),
    ):
        assert classify_mcp_error(transport) is None


@pytest.fixture
def marked(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    seen: list[str | None] = []

    async def _mark(context: Any) -> None:
        seen.append(context.credential_id)

    monkeypatch.setattr(mcp_auth_context, "mark_credential_auth_needed", _mark)
    mcp_auth_context._BUDGETS.clear()
    return seen


async def _tools(monkeypatch: pytest.MonkeyPatch, server: MockMcpServer, **overrides: Any):
    from app.agent_runtime import mcp_tool_loader

    await mcp_cache.clear_mcp_tool_cache()
    monkeypatch.setattr(mcp_tool_loader, "transport_options", lambda _config: server.options())
    tools = await mcp_tool_loader._build_mcp_tools([mcp_config(**overrides)])
    assert [tool.name for tool in tools] == ["post_message"]
    return tools[0]


def _auth_payload(result: Any) -> dict[str, Any]:
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert result.tool_call_id == "call-1"
    assert "Bearer" not in str(result.content) and "user-a" not in str(result.content)
    return result.artifact["mcp_auth_error"]


@pytest.mark.asyncio
async def test_call_401_is_not_retried_and_asks_for_auth(monkeypatch, marked) -> None:
    server = MockMcpServer(call=status(401, INVALID))
    tool = await _tools(monkeypatch, server)

    payload = _auth_payload(await tool.ainvoke(CALL))

    assert server.calls("tools/call") == 1  # the bounded retry never re-sends a 401
    assert payload["kind"] == "auth_required" and payload["status_code"] == 401
    assert payload["interrupt"] == {
        "type": "auth_required",
        "reason": "expired",
        "items": [
            {
                "resource_type": "mcp_server",
                "resource_name": "Slack",
                "tool_names": ["post_message"],
                "credential_id": "22222222-2222-2222-2222-222222222222",
                "fixable_by": "me",
                "connect": {"kind": "oauth"},
            }
        ],
        "allowed_decisions": ["respond"],
    }
    assert marked == ["22222222-2222-2222-2222-222222222222"]


@pytest.mark.asyncio
async def test_401_only_flips_the_actors_own_credential_on_invalid_token(
    monkeypatch, marked
) -> None:
    plain = await _tools(monkeypatch, MockMcpServer(call=status(401)))
    assert _auth_payload(await plain.ainvoke(CALL))["interrupt"]["reason"] == "auth_needed"
    shared = await _tools(
        monkeypatch, MockMcpServer(call=status(401, INVALID)), credential_fixable_by="owner"
    )
    payload = _auth_payload(await shared.ainvoke(CALL))
    assert payload["interrupt"]["items"][0]["fixable_by"] == "owner"
    unbound = await _tools(
        monkeypatch, MockMcpServer(call=status(401, INVALID)), credential_id=None
    )
    _auth_payload(await unbound.ainvoke(CALL))
    assert marked == []  # typed error only: no write to a row the actor does not own


@pytest.mark.asyncio
async def test_discovery_401_becomes_typed_stub_not_unavailable(monkeypatch, marked) -> None:
    server = MockMcpServer(discovery=status(401))
    tool = await _tools(monkeypatch, server, credential_fixable_by="owner")

    payload = _auth_payload(await tool.ainvoke(CALL))

    assert payload["interrupt"]["items"][0]["fixable_by"] == "owner"
    assert server.calls("tools/call") == 0


@pytest.mark.asyncio
async def test_plain_403_is_permission_error_without_interrupt(monkeypatch, marked) -> None:
    server = MockMcpServer(call=status(403))
    tool = await _tools(monkeypatch, server)

    payload = _auth_payload(await tool.ainvoke(CALL))

    assert payload["kind"] == "permission_denied"
    assert payload["interrupt"] is None and marked == []
    assert server.calls("tools/call") == 1


@pytest.mark.asyncio
async def test_insufficient_scope_steps_up_twice_then_fails(monkeypatch, marked) -> None:
    server = MockMcpServer(call=status(403, SCOPE))
    tool = await _tools(monkeypatch, server)
    payloads = [_auth_payload(await tool.ainvoke(CALL)) for _ in range(2)]
    # A ``retry`` resume rebuilds the runtime; the budget still holds per conversation.
    rebuilt = await _tools(monkeypatch, server)
    final = _auth_payload(await rebuilt.ainvoke(CALL))

    for payload in payloads:
        interrupt = payload["interrupt"]
        assert interrupt["reason"] == "scope_upgrade"
        assert interrupt["items"][0]["connect"] == {"kind": "oauth", "scope": "read write"}
    assert final["interrupt"] is None and final["step_up_exhausted"] is True
    assert marked == []
    other = await _tools(monkeypatch, server, conversation_id=str(uuid.uuid4()))
    assert _auth_payload(await other.ainvoke(CALL))["interrupt"] is not None


@pytest.mark.asyncio
async def test_trigger_runs_cap_step_up_per_run_not_agent_wide(monkeypatch, marked) -> None:
    server = MockMcpServer(call=status(403, SCOPE))
    tool = await _tools(monkeypatch, server, run_source="trigger")

    def run(run_id: str) -> RunnableConfig:
        return {"configurable": {"moldy_run_id": run_id}}

    first = [_auth_payload(await tool.ainvoke(CALL, run("r1"))) for _ in range(3)]
    assert [p["interrupt"] is not None for p in first] == [True, True, False]
    assert _auth_payload(await tool.ainvoke(CALL, run("r2")))["interrupt"] is not None


@pytest.mark.asyncio
async def test_oversized_challenged_scope_is_refused(monkeypatch, marked) -> None:
    huge = " ".join(f"s{i}" for i in range(80))
    tool = await _tools(
        monkeypatch,
        MockMcpServer(call=status(403, f'Bearer error="insufficient_scope", scope="{huge}"')),
    )
    payload = _auth_payload(await tool.ainvoke(CALL))
    assert payload["interrupt"] is None and payload["step_up_exhausted"] is True
    assert "s79" not in str(payload)


@pytest.mark.asyncio
async def test_auth_error_drops_the_url_bearing_httpx_cause() -> None:
    from langchain_core.tools import StructuredTool

    async def _call(**_: Any) -> str:
        raise RuntimeError("adapter") from _status_error(401)

    stub = StructuredTool.from_function(coroutine=_call, name="t", description="t")
    wrapped = mcp_cache.MCPToolWithRetry(stub, retry_delay=0)
    with pytest.raises(McpAuthError) as caught:
        await wrapped._arun()
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert caught.value.status_code == 401 and SERVER_URL not in str(caught.value)


@pytest.mark.asyncio
async def test_transport_errors_keep_retry_and_text(monkeypatch, marked) -> None:
    server = MockMcpServer(call=status(503))
    tool = await _tools(monkeypatch, server)

    result = await tool.ainvoke(CALL)

    assert isinstance(result, ToolMessage) and result.status == "success"
    assert str(result.content).startswith("[MCP Tool Error] post_message")
    assert server.calls("tools/call") == 2 and marked == []

    down = MockMcpServer(discovery=lambda request: (_ for _ in ()).throw(httpx.ConnectError("x")))
    stub = await _tools(monkeypatch, down)
    assert "temporarily unavailable" in str((await stub.ainvoke(CALL)).content)


@pytest.mark.asyncio
async def test_direct_invoke_without_call_id_returns_text(marked) -> None:
    from langchain_core.tools import StructuredTool

    async def _call(**_: Any) -> str:
        raise McpAuthError("auth_required", 401)

    stub = StructuredTool.from_function(coroutine=_call, name="t", description="t")
    wrapped = mcp_cache.MCPToolWithRetry(stub, retry_delay=0)

    result = await wrapped.ainvoke({})

    assert result.startswith("[MCP Auth Error] t: auth_required (HTTP 401)")
    assert marked == []  # no context: nothing to mark
