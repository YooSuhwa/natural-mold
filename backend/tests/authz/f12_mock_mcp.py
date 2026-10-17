"""Mock streamable-HTTP MCP server on an ``httpx.MockTransport`` (F-12 tests)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

SERVER_URL = "https://mcp.example.test/mcp"
CallReply = Callable[[httpx.Request], httpx.Response | None]


def _result(request_id: Any, result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": result})


class MockMcpServer:
    """``discovery``/``call`` return a response to fail that step, ``None`` to succeed."""

    def __init__(
        self,
        *,
        discovery: CallReply | None = None,
        call: CallReply | None = None,
    ) -> None:
        self.discovery = discovery
        self.call = call
        self.requests: list[tuple[str, dict[str, str]]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content or b"{}")
        method = body.get("method", "")
        self.requests.append((method, dict(request.headers)))
        if method == "initialize":
            if self.discovery is not None and (failed := self.discovery(request)) is not None:
                return failed
            return _result(
                body["id"],
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock", "version": "1"},
                },
            )
        if method.startswith("notifications/"):
            return httpx.Response(202)
        if method == "tools/list":
            tool = {
                "name": "post_message",
                "description": "post",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
            return _result(body["id"], {"tools": [tool]})
        if method == "tools/call":
            if self.call is not None and (failed := self.call(request)) is not None:
                return failed
            return _result(body["id"], {"content": [{"type": "text", "text": "posted"}]})
        return httpx.Response(400)

    def options(self) -> dict[str, Any]:
        def factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(self.handle),
                headers=headers,
                timeout=timeout or httpx.Timeout(5.0),
                auth=auth,
            )

        return {"httpx_client_factory": factory}

    def calls(self, method: str) -> int:
        return sum(1 for name, _ in self.requests if name == method)


def status(code: int, challenge: str | None = None) -> CallReply:
    headers = {"WWW-Authenticate": challenge} if challenge else {}
    return lambda _request: httpx.Response(code, headers=headers)


def mcp_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "definition_key": "mcp",
        "name": "post_message",
        "mcp_server_url": SERVER_URL,
        "mcp_server_id": "11111111-1111-1111-1111-111111111111",
        "mcp_server_name": "Slack",
        "mcp_tool_name": "post_message",
        "mcp_transport_headers": {"Authorization": "Bearer user-a"},
        "credential_id": "22222222-2222-2222-2222-222222222222",
        "credential_mode": "per_user",
        "credential_fixable_by": "me",
        "credentials": {"access_token": "user-a", "scope": "read"},
        "credential_destination": {"allowed_hosts": ["mcp.example.test"], "empty_ok": False},
        "conversation_id": "33333333-3333-3333-3333-333333333333",
        "tenant_id": "44444444-4444-4444-4444-444444444444",
        "org_id": "55555555-5555-5555-5555-555555555555",
        "credential_subject_user_id": "66666666-6666-6666-6666-666666666666",
        "user_id": "66666666-6666-6666-6666-666666666666",
    }
    config.update(overrides)
    return config
