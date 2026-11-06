"""The runtime's HTTP app: one MCP server per path, each behind run-scoped tokens.

* ``/healthz``: liveness for Compose and the operator's system status.
* ``/mcp/system/diagnostics``: a ``whoami`` tool that answers with the verified token
  claims, so the backend can check the whole path (sign → call → verify) end to end.

Spore (``/mcp/openapi/{id}``) and Taproot (``/mcp/db/{id}``) servers are added here by their
own work items; each path checks that the token was made for it. Stateless: no session
store, so replicas need nothing shared.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from moldy_mcp_runtime import tokens

DIAGNOSTICS = "system/diagnostics"


class RunTokenMiddleware:
    """Refuse a request without a valid token for this server; keep its claims."""

    def __init__(self, app: ASGIApp, *, server: str) -> None:
        self.app = app
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        scheme, _, value = header.partition(" ")
        try:
            if scheme.lower() != "bearer" or not value:
                raise tokens.TokenError("missing token")
            claims = tokens.verify(value, server=self.server)
        except tokens.TokenError:
            response: Response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["moldy_claims"] = claims
        await self.app(scope, receive, send)


def run_claims() -> tokens.RunClaims:
    request = get_http_request()
    claims = getattr(request.state, "moldy_claims", None)
    if not isinstance(claims, tokens.RunClaims):  # only RunTokenMiddleware sets it
        raise TypeError("tool reached without a verified run token")
    return claims


def diagnostics_server() -> FastMCP:
    server = FastMCP("moldy-diagnostics")

    @server.tool(annotations={"readOnlyHint": True})
    def whoami() -> dict[str, Any]:
        """Who this call runs as, from the verified run token."""
        return run_claims().as_dict()

    return server


async def healthz(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def create_app() -> Starlette:
    tokens.secret()  # fail at start, not at the first call, when the secret is missing
    diagnostics = diagnostics_server().http_app(path="/", stateless_http=True, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with diagnostics.lifespan(diagnostics):
            yield

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Mount(f"/mcp/{DIAGNOSTICS}", app=RunTokenMiddleware(diagnostics, server=DIAGNOSTICS)),
        ],
        lifespan=lifespan,
    )
