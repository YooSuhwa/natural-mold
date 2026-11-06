"""A real MCP server over HTTP (FastMCP 4) for client tests: Streamable HTTP and SSE.

``serve(server)`` runs it on a free localhost port in a thread; ``RequestLog`` records each
HTTP request's headers so tests can check what the backend sent.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass
class RequestLog:
    headers: list[dict[str, str]] = field(default_factory=list)


class _Recorder:
    def __init__(self, app: ASGIApp, log: RequestLog) -> None:
        self.app = app
        self.log = log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            self.log.headers.append(
                {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            )
        await self.app(scope, receive, send)


def weather_server() -> FastMCP:
    server = FastMCP("weather", version="1.0")

    @server.tool(meta={"ui": {"resourceUri": "ui://weather/dashboard"}})
    def search_confluence(query: str) -> ToolResult:
        return ToolResult(
            content=[TextContent(type="text", text=f"Confluence page result for {query}")],
            structured_content={"query": query},
            meta={"viewUUID": "view-1"},
        )

    @server.resource(
        "ui://weather/dashboard",
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {"csp": {"connectDomains": ["https://api.weather.test"]}, "prefersBorder": True}
        },
    )
    def dashboard() -> str:
        return "<!doctype html><title>Weather</title>"

    return server


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve(server: FastMCP, transport: str = "streamable-http") -> Iterator[tuple[str, RequestLog]]:
    """Yield the endpoint URL (``/mcp`` or ``/sse``) and the request log."""
    log = RequestLog()
    path = "/sse" if transport == "sse" else "/mcp"
    app: Any = _Recorder(server.http_app(transport=transport, path=path), log)  # type: ignore[arg-type]
    port = _free_port()
    runner = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not runner.started:
        if time.monotonic() > deadline:
            raise RuntimeError("fixture MCP server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}{path}", log
    finally:
        runner.should_exit = True
        thread.join(timeout=5)
