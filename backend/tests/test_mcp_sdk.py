"""``app.mcp.sdk`` against a real MCP server over HTTP (tests/mcp_http_fixture.py)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastmcp import FastMCP
from starlette.responses import JSONResponse

from app.agent_runtime.mcp_auth_errors import classify_mcp_error
from app.agent_runtime.mcp_cache import _unwrap_mcp_result_artifact
from app.mcp import sdk
from tests.mcp_http_fixture import serve

CHALLENGE = 'Bearer error="insufficient_scope", scope="write"'


def _echo_server() -> FastMCP:
    server = FastMCP("echo")

    @server.tool(annotations={"readOnlyHint": True})
    def lookup(query: str, api_key: str) -> dict[str, str]:
        return {"query": query, "api_key": api_key}

    return server


async def test_argument_auth_is_merged_into_the_call_and_hidden_from_the_model() -> None:
    with serve(_echo_server()) as (url, _log):
        transport = lambda: sdk.http_transport("streamable_http", url)  # noqa: E731
        _info, [definition] = await sdk.discover(transport())
        tool = sdk.as_langchain_tool(definition, transport, inject={"api_key": "k-123"})

        assert set(tool.args) == {"query"}  # the model never sees api_key
        content, artifact = await tool.coroutine(query="moldy")  # type: ignore[misc]

    restored, _result_meta = _unwrap_mcp_result_artifact(artifact)
    assert restored == {"structured_content": {"query": "moldy", "api_key": "k-123"}}
    # Annotations keep their wire names; tools/risk.py reads readOnlyHint.
    assert tool.metadata is not None and tool.metadata["readOnlyHint"] is True
    assert "read_only_hint" not in tool.metadata
    assert content and content[0]["type"] == "text"


async def test_the_model_cannot_override_injected_auth() -> None:
    with serve(_echo_server()) as (url, _log):
        transport = lambda: sdk.http_transport("streamable_http", url)  # noqa: E731
        _info, [definition] = await sdk.discover(transport())
        tool = sdk.as_langchain_tool(definition, transport, inject={"api_key": "k-123"})
        _content, artifact = await tool.coroutine(query="q", api_key="from-model")  # type: ignore[misc]

    restored, _result_meta = _unwrap_mcp_result_artifact(artifact)
    assert restored == {"structured_content": {"query": "q", "api_key": "k-123"}}


@pytest.fixture
def refusing_server(request: pytest.FixtureRequest) -> Iterator[str]:
    """An HTTP endpoint that answers every request with ``request.param`` and a challenge."""
    status = request.param

    async def refuse(scope, receive, send):  # type: ignore[no-untyped-def]
        headers = {"WWW-Authenticate": CHALLENGE}
        await JSONResponse({}, status_code=status, headers=headers)(scope, receive, send)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    runner = uvicorn.Server(uvicorn.Config(refuse, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    while not runner.started:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        runner.should_exit = True
        thread.join(timeout=5)


@pytest.mark.parametrize("refusing_server", [401, 403], indirect=True)
async def test_an_http_refusal_keeps_its_status_and_challenge(refusing_server: str) -> None:
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await sdk.discover(sdk.http_transport("streamable_http", refusing_server))

    error = classify_mcp_error(caught.value)
    status = caught.value.response.status_code
    assert error is not None and error.status_code == status in (401, 403)
    assert caught.value.response.headers["WWW-Authenticate"] == CHALLENGE
    if status == 403:
        assert error.kind == "insufficient_scope"
