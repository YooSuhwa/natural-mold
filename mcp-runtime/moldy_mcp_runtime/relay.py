"""The MCP gateway relay: the backend's only way to an external MCP server (plan 8.2, D10).

The backend sends every Streamable HTTP and SSE request for an external MCP server to
``/relay/<path>`` with a relay token (``server="relay"``). The token seals the upstream
server's URL and headers, credentials included (``tokens.open_upstream``), plus what this
connection may do:

* ``tools``: the tool names ``tools/call`` may name (``null``: none may be called, e.g. a
  discovery). A call outside the list is answered here with a JSON-RPC error and never
  reaches the server.
* ``resources``: whether ``resources/read`` is allowed (MCP Apps).
* ``https_only``: a credentialed connection never goes out over plain http.

The request goes to the upstream origin at the same path (an SSE server's message endpoint
included: its ``endpoint`` event is rewritten to point back here). Redirects are refused,
so a server cannot move a credentialed request elsewhere. Upstream 401/403 answers and
their challenges pass through unchanged for the backend's auth handling; the relay's own
refusals use other statuses so they are never taken for the upstream's. Nothing is
retried and nothing is stored: replicas share nothing.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from collections.abc import AsyncIterator, Iterable
from typing import Any, Final
from urllib.parse import urlsplit

import anyio
import httpx2
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from moldy_mcp_runtime import tokens

logger = logging.getLogger(__name__)

RELAY: Final = "relay"
MAX_BODY_BYTES: Final = 8 * 1024 * 1024
TOOL_NOT_ALLOWED: Final = -32001
_HOP_BY_HOP: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
_TIMEOUT: Final = httpx2.Timeout(30.0, read=300.0)


class Refused(Exception):
    def __init__(self, status: int, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


def _refusal(status: int, error: str) -> Response:
    return JSONResponse({"error": error}, status_code=status, headers={"X-Moldy-Relay": "refused"})


def _jsonrpc_error(request_id: Any, message: str) -> Response:
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": TOOL_NOT_ALLOWED, "message": message},
    }
    return JSONResponse(body, status_code=200, headers={"X-Moldy-Relay": "refused"})


def _claims(request: Request) -> tuple[tokens.RunClaims, dict[str, Any]]:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not value:
            raise tokens.TokenError("missing token")
        claims = tokens.verify(value, server=RELAY)
        if claims.upstream is None:
            raise tokens.TokenError("no upstream")
        upstream = tokens.open_upstream(claims.upstream, claims.token_id)
    except tokens.TokenError as exc:
        raise Refused(407, "relay token refused") from exc
    return claims, upstream


async def _check_destination(host: str) -> None:
    """Link-local and cloud metadata addresses are never relayed to."""
    try:
        infos = await anyio.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise Refused(502, "upstream host does not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_link_local or address.is_unspecified or address.is_multicast:
            raise Refused(502, "upstream address blocked")


def _forward_headers(request: Request, upstream_headers: dict[str, Any]) -> dict[str, str]:
    out = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP
        and key.lower() != "authorization"
        and not key.lower().startswith("x-moldy-")
    }
    out.update({str(k): str(v) for k, v in upstream_headers.items()})
    return out


BLOCKED_PREFIX: Final = "moldy-gateway-blocked:"


def _check_call(
    body: bytes, request: Request, upstream: dict[str, Any]
) -> tuple[Response | None, bytes]:
    """Refuse a call this connection may not make; returns ``(answer, body to relay)``.

    Streamable HTTP gets the JSON-RPC error here. An SSE connection answers on its event
    stream, which the relay does not hold, so the request is relayed renamed instead: the
    server cannot run a tool or read a resource by a name that does not exist, and says so on
    the stream.
    """
    try:
        message = json.loads(body)
    except ValueError as exc:
        raise Refused(400, "body is not JSON") from exc
    if isinstance(message, list):
        raise Refused(400, "JSON-RPC batches are not relayed")
    if not isinstance(message, dict):
        raise Refused(400, "body is not a JSON-RPC message")
    method = message.get("method")
    raw_params = message.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    header_method = request.headers.get("mcp-method")
    header_name = request.headers.get("mcp-name")
    if header_method is not None and header_method != method:
        raise Refused(400, "Mcp-Method does not match the body")
    reason: str | None = None
    field = ""
    if method == "tools/call":
        name = params.get("name")
        if header_name is not None and header_name != name:
            raise Refused(400, "Mcp-Name does not match the body")
        allowed = upstream.get("tools")
        if not isinstance(allowed, list) or name not in allowed:
            reason, field = f"tool '{name}' is not allowed for this run", "name"
    elif method == "resources/read" and upstream.get("resources") is not True:
        reason, field = "resource reads are not allowed for this run", "uri"
    if reason is None:
        return None, body
    if upstream.get("transport") != "sse":
        return _jsonrpc_error(message.get("id"), reason), body
    renamed = {**message, "params": {**params, field: f"{BLOCKED_PREFIX}{params.get(field)}"}}
    return None, json.dumps(renamed).encode()


def _rewrite_endpoint(data: str, origin: str, prefix: str) -> str:
    """An SSE ``endpoint`` (relative or same-origin URL) pointed back at this relay."""
    value = data.strip()
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        if f"{parts.scheme}://{parts.netloc}" != origin:
            raise Refused(502, "upstream endpoint on another origin")
        value = parts.path + (f"?{parts.query}" if parts.query else "")
    if not value.startswith("/"):
        value = "/" + value
    return prefix + value


async def _sse_lines(response: httpx2.Response, origin: str, prefix: str) -> AsyncIterator[bytes]:
    event = ""
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line == "":
            event = ""
        elif event == "endpoint" and line.startswith("data:"):
            line = "data: " + _rewrite_endpoint(line[len("data:") :], origin, prefix)
        yield (line + "\n").encode()


async def _raw(response: httpx2.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_raw():
        yield chunk


def _response_headers(headers: Iterable[tuple[str, str]], *, decoded: bool) -> dict[str, str]:
    skip = _HOP_BY_HOP | ({"content-encoding"} if decoded else set())
    return {k: v for k, v in headers if k.lower() not in skip}


async def relay(scope: Scope, receive: Receive, send: Send) -> None:
    request = Request(scope, receive)
    try:
        response = await _relay(request)
    except Refused as refused:
        logger.info("relay refused: %s", refused.error)
        response = _refusal(refused.status, refused.error)
    await response(scope, receive, send)


async def _relay(request: Request) -> Response:
    claims, upstream = _claims(request)
    base = urlsplit(str(upstream.get("url") or ""))
    if base.scheme not in ("http", "https") or not base.hostname:
        raise Refused(502, "invalid upstream")
    if upstream.get("https_only") and base.scheme != "https":
        raise Refused(502, "credentials only go out over https")
    await _check_destination(base.hostname)
    origin = f"{base.scheme}://{base.netloc}"

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise Refused(413, "request too large")
    if request.method == "POST" and body:
        refused, body = _check_call(body, request, upstream)
        if refused is not None:
            return refused

    path = request.url.path[len(request.scope.get("root_path", "")) :] or "/"
    target = origin + path + (f"?{request.url.query}" if request.url.query else "")
    headers = _forward_headers(request, dict(upstream.get("headers") or {}))
    client = httpx2.AsyncClient(timeout=_TIMEOUT, follow_redirects=False, trust_env=True)
    try:
        outgoing = client.build_request(request.method, target, headers=headers, content=body)
        answered = await client.send(outgoing, stream=True)
    except httpx2.HTTPError as exc:
        await client.aclose()
        logger.info("relay upstream error for run %s: %s", claims.run_id, type(exc).__name__)
        raise Refused(502, "upstream not reachable") from exc
    if answered.is_redirect:
        await answered.aclose()
        await client.aclose()
        raise Refused(502, "upstream redirect refused")

    async def close() -> None:
        await answered.aclose()
        await client.aclose()

    event_stream = answered.headers.get("content-type", "").startswith("text/event-stream")
    rewrite = event_stream and request.method == "GET"
    prefix = str(request.scope.get("root_path", ""))
    content = _sse_lines(answered, origin, prefix) if rewrite else _raw(answered)
    return StreamingResponse(
        content,
        status_code=answered.status_code,
        headers=_response_headers(answered.headers.multi_items(), decoded=rewrite),
        background=BackgroundTask(close),
    )


__all__ = ["RELAY", "relay"]
