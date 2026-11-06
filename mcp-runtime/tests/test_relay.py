"""The gateway relay (relay.py, D10): only what the relay token allows reaches the server."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from tests.conftest import relay_token, serve

SEEN: list[dict[str, str]] = []
SSE_SERVER: FastMCP | None = None


class _Record:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            SEEN.append({k.decode().lower(): v.decode() for k, v in scope["headers"]})
        await self.app(scope, receive, send)


def _mail_server() -> FastMCP:
    server = FastMCP("mail")
    calls: list[str] = []

    @server.tool
    def send(to: str) -> str:
        calls.append(f"send:{to}")
        return f"sent to {to}"

    @server.tool
    def delete_all() -> str:
        calls.append("delete_all")
        return "deleted"

    @server.resource("ui://mail/app", mime_type="text/html;profile=mcp-app")
    def app() -> str:
        return "<p>mail</p>"

    server.calls = calls  # type: ignore[attr-defined]
    return server


async def _refuse(_request: Request) -> Response:
    return JSONResponse(
        {}, status_code=401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
    )


async def _moved(_request: Request) -> Response:
    return RedirectResponse("https://elsewhere.example/mcp", status_code=307)


@pytest.fixture
def upstream() -> Iterator[tuple[str, FastMCP]]:
    SEEN.clear()
    mail = _mail_server()
    http = mail.http_app(path="/mcp", stateless_http=True, json_response=True)
    global SSE_SERVER
    SSE_SERVER = _mail_server()
    sse = SSE_SERVER.http_app(transport="sse", path="/sse")
    app = Starlette(
        routes=[
            Route("/refuse", _refuse, methods=["POST"]),
            Route("/moved", _moved, methods=["POST"]),
            Mount("/legacy", app=sse),
            Mount("/", app=http),
        ],
        lifespan=http.lifespan,
    )
    with serve(_Record(app)) as url:
        yield url, mail


def _upstream(url: str, **extra: Any) -> dict[str, Any]:
    return {
        "url": url,
        "transport": "streamable_http",
        "headers": {"Authorization": "Bearer upstream-secret"},
        "tools": ["send"],
        "resources": False,
        "https_only": False,
        **extra,
    }


def _via(runtime_url: str, path: str, token: str, *, sse: bool = False) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    if sse:
        return SSETransport(f"{runtime_url}/relay{path}", headers=headers)
    return StreamableHttpTransport(f"{runtime_url}/relay{path}", headers=headers)


async def test_an_allowed_call_goes_through_with_the_sealed_headers_only(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, mail = upstream
    token = relay_token(_upstream(f"{base}/mcp"))

    async with Client(_via(runtime_url, "/mcp", token)) as client:
        names = {tool.name for tool in await client.list_tools()}
        result = await client.call_tool("send", {"to": "a@example.com"})

    assert names == {"send", "delete_all"}  # listing is allowed; calling is not
    assert result.content[0].text == "sent to a@example.com"  # type: ignore[union-attr]
    assert mail.calls == ["send:a@example.com"]  # type: ignore[attr-defined]
    assert {h.get("authorization") for h in SEEN} == {"Bearer upstream-secret"}
    assert not any(k.startswith("x-moldy") for h in SEEN for k in h)


async def test_a_tool_outside_the_run_never_reaches_the_server(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, mail = upstream
    token = relay_token(_upstream(f"{base}/mcp"))

    async with Client(_via(runtime_url, "/mcp", token)) as client:
        with pytest.raises(MCPError, match="'delete_all' is not allowed for this run"):
            await client.call_tool("delete_all", {})
        with pytest.raises(MCPError, match="resource reads are not allowed for this run"):
            await client.read_resource("ui://mail/app")

    assert mail.calls == []  # type: ignore[attr-defined]


async def test_resource_reads_need_the_resources_grant(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, _mail = upstream
    token = relay_token(_upstream(f"{base}/mcp", resources=True, tools=[]))

    async with Client(_via(runtime_url, "/mcp", token)) as client:
        [content] = await client.read_resource("ui://mail/app")

    assert content.text == "<p>mail</p>"  # type: ignore[union-attr]


async def test_an_sse_server_works_through_the_rewritten_endpoint(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, _mail = upstream
    sse_calls = SSE_SERVER.calls  # type: ignore[attr-defined]
    token = relay_token(_upstream(f"{base}/legacy/sse", transport="sse"))

    async with Client(_via(runtime_url, "/legacy/sse", token, sse=True)) as client:
        result = await client.call_tool("send", {"to": "b@example.com"})
        # The relay renames a refused call: the server cannot run it and answers on the stream.
        with pytest.raises(ToolError, match="moldy-gateway-blocked:delete_all"):
            await client.call_tool("delete_all", {})

    assert result.content[0].text == "sent to b@example.com"  # type: ignore[union-attr]
    assert sse_calls == ["send:b@example.com"]


@pytest.mark.parametrize(
    ("token_kind", "status"),
    [("missing", 407), ("wrong_server", 407), ("tampered", 407)],
)
async def test_without_a_valid_relay_token_nothing_is_relayed(
    runtime_url: str, upstream: tuple[str, FastMCP], token_kind: str, status: int
) -> None:
    from tests.conftest import make_token

    base, _mail = upstream
    tokens = {
        "missing": None,
        "wrong_server": make_token("system/diagnostics"),
        "tampered": relay_token(_upstream(f"{base}/mcp"), sealed="AAAA" * 10),
    }
    headers = {"Authorization": f"Bearer {tokens[token_kind]}"} if tokens[token_kind] else {}
    async with httpx.AsyncClient() as http:
        response = await http.post(
            f"{runtime_url}/relay/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1}
        )

    assert response.status_code == status
    assert SEEN == []


async def test_upstream_401_passes_through_but_redirects_and_plain_http_do_not(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, _mail = upstream
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

    async def post(path: str, **extra: Any) -> httpx.Response:
        token = relay_token(_upstream(f"{base}{path}", **extra))
        async with httpx.AsyncClient() as http:
            return await http.post(
                f"{runtime_url}/relay{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )

    refused = await post("/refuse")
    assert refused.status_code == 401
    assert refused.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    moved = await post("/moved")
    assert (moved.status_code, moved.json()["error"]) == (502, "upstream redirect refused")
    plain = await post("/mcp", https_only=True)
    assert (plain.status_code, plain.json()["error"]) == (502, "credentials only go out over https")


async def test_batches_and_mismatched_headers_are_refused(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, mail = upstream
    token = relay_token(_upstream(f"{base}/mcp"))
    auth = {"Authorization": f"Bearer {token}"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "send"}}
    async with httpx.AsyncClient() as http:
        batch = await http.post(f"{runtime_url}/relay/mcp", headers=auth, json=[call])
        lied = await http.post(
            f"{runtime_url}/relay/mcp",
            headers={**auth, "Mcp-Method": "tools/call", "Mcp-Name": "delete_all"},
            json=call,
        )

    assert (batch.status_code, lied.status_code) == (400, 400)
    assert mail.calls == []  # type: ignore[attr-defined]


async def test_the_relay_token_never_reaches_a_server_without_its_own_auth(
    runtime_url: str, upstream: tuple[str, FastMCP]
) -> None:
    base, _mail = upstream
    token = relay_token(_upstream(f"{base}/mcp", headers={"X-Tenant": "one"}))

    async with Client(_via(runtime_url, "/mcp", token)) as client:
        await client.list_tools()

    assert SEEN and all("authorization" not in h for h in SEEN)
    assert {h.get("x-tenant") for h in SEEN} == {"one"}
