"""The one module that speaks the MCP SDK (mcp 2.x through FastMCP 4; plan 8.3, D12).

Everything else in the backend works with the plain values here (``ToolDefinition``,
``ServerInfo``, ``CallResult``) and LangChain tools, so a change in the SDK, or in the beta
``langchain.mcp`` namespace, stays inside this file (``tests/test_mcp_sdk_boundary.py``).

* Transports: Streamable HTTP and SSE go through ``outbound_http_client`` (shared trust
  store, proxies, the credentialed https-only and destination guards) via a small bridge,
  because the SDK uses ``httpx2`` while the backend's guards are built on ``httpx``. A
  credentialed connection follows redirects only on the same host.
* Tools keep the shape ``langchain-mcp-adapters`` gave them (content blocks plus a
  ``structured_content`` artifact, annotations and ``_meta`` in ``metadata``), so risk
  levels and MCP Apps read the same values. Argument auth (``inject``) is merged into the
  call over the model's arguments and never appears in the model's schema. Result
  ``_meta`` travels in the envelope ``mcp_cache`` unwraps for MCP Apps.
* Servers asking for input mid-call (elicitation) are not offered the capability yet, so a
  call that needs it fails instead of waiting.
"""

from __future__ import annotations

import datetime
import weakref
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import httpx2
from fastmcp.client import Client
from fastmcp.client.transports import ClientTransport, SSETransport, StdioTransport
from fastmcp.client.transports.http import StreamableHttpTransport
from langchain_core.messages.content import (
    create_file_block,
    create_image_block,
    create_text_block,
)
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp import types as mcp_types
from mcp.shared.exceptions import MCPError
from pydantic import AnyUrl

from app.mcp import gateway
from app.mcp.gateway import GatewayScope, GatewayUnavailable
from app.outbound.client import DEFAULT_TIMEOUT, outbound_http_client
from app.outbound.destination import OutboundBlockedError

Transport = Literal["streamable_http", "sse"]
RESULT_ENVELOPE = "__moldy_mcp_result__"
# The MCP SDK's own defaults: a server may hold a response stream open.
_READ_TIMEOUT = 300.0


@dataclass(frozen=True, slots=True)
class ServerInfo:
    name: str | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One tool as the server described it (what discovery stores and reviews)."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] | None = None

    @classmethod
    def from_sdk(cls, tool: mcp_types.Tool) -> ToolDefinition:
        # Wire names (readOnlyHint, destructiveHint): tools/risk.py reads those.
        annotations = (
            tool.annotations.model_dump(exclude_none=True, by_alias=True)
            if tool.annotations
            else {}
        )
        return cls(
            name=tool.name,
            description=tool.description or "",
            input_schema=dict(tool.input_schema or {}),
            annotations=annotations,
            meta=dict(tool.meta) if tool.meta is not None else None,
        )

    def langchain_metadata(self) -> dict[str, Any] | None:
        """The ``metadata`` langchain-mcp-adapters attached: annotations plus ``_meta``."""
        metadata: dict[str, Any] = dict(self.annotations)
        if self.meta is not None:
            metadata["_meta"] = self.meta
        return metadata or None


# The relay's own JSON-RPC error for a call the run may not make (mcp-runtime relay.py).
GATEWAY_REFUSED_CODE = -32001


class GatewayRefused(Exception):
    """The gateway refused this call for this run: re-sending it changes nothing."""


@dataclass(frozen=True, slots=True)
class CallResult:
    content: list[Any]
    structured_content: dict[str, Any] | None
    meta: dict[str, Any] | None
    is_error: bool


# --- httpx2 -> httpx bridge -----------------------------------------------------------


class _Stream(httpx2.AsyncByteStream):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._response.is_stream_consumed:  # a response built with its body (test seams)
            yield self._response.content
            return
        async for chunk in self._response.aiter_raw():
            yield chunk

    async def aclose(self) -> None:
        await self._response.aclose()


@dataclass(slots=True)
class _Refusal:
    status_code: int
    headers: list[tuple[str, str]]
    url: str


class HttpxBridge(httpx2.AsyncBaseTransport):
    """Send the SDK's ``httpx2`` requests through a guarded ``httpx`` client.

    401 and 403 answers are noted in ``refusals``: the SDK turns them into a generic
    JSON-RPC error, and ``session`` gives the caller the status back (F-12 auth errors).
    """

    def __init__(self, inner: httpx.AsyncClient, refusals: list[_Refusal] | None = None) -> None:
        self._inner = inner
        self._refusals = refusals if refusals is not None else []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        outgoing = self._inner.build_request(
            request.method,
            str(request.url),
            headers=list(request.headers.multi_items()),
            content=body,
        )
        try:
            response = await self._inner.send(outgoing, stream=True)
        except OutboundBlockedError:
            raise  # refused before any byte left: keep the reason as is
        except httpx.TimeoutException as exc:
            raise httpx2.ReadTimeout(str(exc), request=request) from exc
        except httpx.TransportError as exc:
            raise httpx2.ConnectError(str(exc), request=request) from exc
        if response.status_code in (401, 403):
            self._refusals.append(
                _Refusal(
                    response.status_code, list(response.headers.multi_items()), str(request.url)
                )
            )
        return httpx2.Response(
            response.status_code,
            headers=list(response.headers.multi_items()),
            stream=_Stream(response),
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def _timeout(value: httpx2.Timeout | None) -> httpx.Timeout:
    if value is None:
        return httpx.Timeout(DEFAULT_TIMEOUT, read=_READ_TIMEOUT)
    return httpx.Timeout(connect=value.connect, read=value.read, write=value.write, pool=value.pool)


async def _same_host_redirects_only(response: httpx2.Response) -> None:
    """A credentialed connection never follows a redirect to another host."""
    if not response.is_redirect:
        return
    location = response.headers.get("location")
    if location is None:
        return
    target = response.request.url.join(location)
    origin = response.request.url
    if (target.host, target.port) != (origin.host, origin.port):
        raise OutboundBlockedError(f"redirect to another host '{target.host}' blocked")


def http_client_factory(
    *, credentialed: bool, refusals: list[_Refusal] | None = None
) -> Callable[..., httpx2.AsyncClient]:
    """The SDK's ``httpx_client_factory`` over ``outbound_http_client``.

    A credentialed connection is https only (``CREDENTIAL_HTTP_ALLOWED_HOSTS`` excepted)
    and follows redirects only on the same host.
    """

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        follow_redirects: bool = False,
    ) -> httpx2.AsyncClient:
        inner = outbound_http_client(timeout=_timeout(timeout), https_only=credentialed)
        hooks = {"response": [_same_host_redirects_only]} if credentialed else {}
        return httpx2.AsyncClient(
            headers=headers,
            timeout=timeout or httpx2.Timeout(DEFAULT_TIMEOUT, read=_READ_TIMEOUT),
            auth=auth,
            follow_redirects=follow_redirects,
            event_hooks=hooks,
            transport=HttpxBridge(inner, refusals),
        )

    return factory


class _RelayAuth(httpx2.Auth):
    """A fresh relay token on every request, so a long run never carries an expired one."""

    def __init__(self, relay: gateway.Relay) -> None:
        self._relay = relay

    def auth_flow(self, request: httpx2.Request) -> Iterator[httpx2.Request]:
        request.headers["Authorization"] = f"Bearer {self._relay.token()}"
        yield request


def http_transport(
    transport: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    credentialed: bool = False,
    scope: GatewayScope | None = None,
) -> ClientTransport:
    """A transport to an external MCP server.

    With ``MCP_GATEWAY_ENABLED`` it goes to the runtime's relay instead (decision D10): the
    upstream URL and ``headers`` are sealed into per-request relay tokens with ``scope``, and
    nothing connects to the server from here.
    """
    if gateway.enabled():
        relay = gateway.relay(
            transport, url, headers, credentialed=credentialed, scope=scope or GatewayScope()
        )
        return _http_transport(
            transport, relay.url, None, credentialed=False, auth=_RelayAuth(relay)
        )
    return _http_transport(transport, url, headers, credentialed=credentialed)


def runtime_transport(url: str, headers: Mapping[str, str]) -> ClientTransport:
    """A transport to one of the runtime's own servers (never relayed)."""
    return _http_transport("streamable_http", url, headers, credentialed=False)


def _http_transport(
    transport: str,
    url: str,
    headers: Mapping[str, str] | None,
    *,
    credentialed: bool,
    auth: httpx2.Auth | None = None,
) -> ClientTransport:
    refusals: list[_Refusal] = []
    factory = http_client_factory(credentialed=credentialed, refusals=refusals)
    built: ClientTransport
    if transport == "sse":
        built = SSETransport(
            url, headers=dict(headers or {}), auth=auth, httpx_client_factory=factory
        )
    elif transport == "streamable_http":
        built = StreamableHttpTransport(
            url, headers=dict(headers or {}), auth=auth, httpx_client_factory=factory
        )
    else:
        raise ValueError(f"transport '{transport}' is not an HTTP transport")
    _REFUSALS[built] = refusals
    return built


_REFUSALS: weakref.WeakKeyDictionary[ClientTransport, list[_Refusal]] = weakref.WeakKeyDictionary()


def _status_error(refusal: _Refusal) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", refusal.url)
    response = httpx.Response(refusal.status_code, headers=refusal.headers, request=request)
    return httpx.HTTPStatusError(
        f"MCP server answered HTTP {refusal.status_code}", request=request, response=response
    )


def stdio_transport(command: str, args: list[str], env: dict[str, str] | None) -> ClientTransport:
    return StdioTransport(command=command, args=args, env=env or None)


def _client(transport: ClientTransport, request_timeout: float | None) -> Client[Any]:
    return Client(transport, timeout=request_timeout, init_timeout=request_timeout)


@asynccontextmanager
async def session(
    transport: ClientTransport, *, request_timeout: float | None = None
) -> AsyncIterator[Client[Any]]:
    """An initialized client for one exchange.

    A failure after the server answered 401/403 is raised as ``httpx.HTTPStatusError``
    with that status and its ``WWW-Authenticate`` challenge, as before the SDK move.
    """
    refusals = _REFUSALS.get(transport)
    try:
        async with _client(transport, request_timeout) as client:
            yield client
    except Exception as exc:
        if refusals:
            raise _status_error(refusals[0]) from exc
        if isinstance(exc, httpx2.HTTPStatusError):
            raise _status_error(
                _Refusal(
                    exc.response.status_code,
                    list(exc.response.headers.multi_items()),
                    str(exc.request.url),
                )
            ) from exc
        raise


async def discover(
    transport: ClientTransport, *, request_timeout: float | None = None
) -> tuple[ServerInfo, list[ToolDefinition]]:
    async with session(transport, request_timeout=request_timeout) as client:
        tools = await client.list_tools()
        info = client.server_info
        server = ServerInfo(info.name, info.version) if info is not None else ServerInfo()
        return server, [ToolDefinition.from_sdk(tool) for tool in tools]


async def call_tool(
    transport: ClientTransport,
    name: str,
    arguments: dict[str, Any],
    *,
    request_timeout: float | None = None,
) -> CallResult:
    async with session(transport, request_timeout=request_timeout) as client:
        try:
            result = await client.call_tool(name, arguments, raise_on_error=False)
        except MCPError as exc:
            if exc.error.code == GATEWAY_REFUSED_CODE:
                raise GatewayRefused(exc.error.message) from None
            raise
    return CallResult(
        content=list(result.content),
        structured_content=result.structured_content,
        meta=result.meta,
        is_error=result.is_error,
    )


async def read_resource(
    transport: ClientTransport, uri: str, *, request_timeout: float | None = None
) -> list[Any]:
    async with session(transport, request_timeout=request_timeout) as client:
        return list(await client.read_resource(AnyUrl(uri)))


# --- LangChain tools ------------------------------------------------------------------


def _content_block(content: Any) -> Any:
    if isinstance(content, mcp_types.TextContent):
        return create_text_block(text=content.text)
    if isinstance(content, mcp_types.ImageContent):
        return create_image_block(base64=content.data, mime_type=content.mime_type)
    if isinstance(content, mcp_types.ResourceLink):
        mime = content.mime_type or None
        if mime and mime.startswith("image/"):
            return create_image_block(url=str(content.uri), mime_type=mime)
        return create_file_block(url=str(content.uri), mime_type=mime)
    if isinstance(content, mcp_types.EmbeddedResource):
        resource = content.resource
        if isinstance(resource, mcp_types.TextResourceContents):
            return create_text_block(text=resource.text)
        mime = resource.mime_type or None
        if mime and mime.startswith("image/"):
            return create_image_block(base64=resource.blob, mime_type=mime)
        return create_file_block(base64=resource.blob, mime_type=mime)
    raise ValueError(f"Unsupported MCP content type: {type(content).__name__}")


def _artifact(result: CallResult) -> dict[str, Any] | None:
    if result.meta is not None:
        return {
            "structured_content": {
                RESULT_ENVELOPE: {
                    "structured_content": result.structured_content,
                    "result_meta": result.meta,
                }
            }
        }
    if result.structured_content is not None:
        return {"structured_content": result.structured_content}
    return None


def as_langchain_tool(
    definition: ToolDefinition,
    make_transport: Callable[[], ClientTransport],
    *,
    inject: Mapping[str, Any] | None = None,
    request_timeout: float | None = None,
) -> BaseTool:
    """A LangChain tool that opens one session per call (``make_transport`` each time).

    ``inject`` (argument auth) is removed from the schema the model sees and overrides any
    value the model sends for the same field.
    """
    hidden = set(inject or {})
    schema = dict(definition.input_schema)
    if hidden:
        properties = dict(schema.get("properties") or {})
        for key in hidden:
            properties.pop(key, None)
        schema["properties"] = properties
        schema["required"] = [r for r in schema.get("required") or [] if r not in hidden]

    async def call(**arguments: Any) -> tuple[list[Any], dict[str, Any] | None]:
        merged = {**arguments, **dict(inject or {})}  # injected auth always wins
        result = await call_tool(
            make_transport(), definition.name, merged, request_timeout=request_timeout
        )
        blocks = [_content_block(item) for item in result.content]
        if result.is_error:
            texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            raise ToolException("\n".join(texts) if texts else str(blocks))
        return blocks, _artifact(result)

    return StructuredTool(
        name=definition.name,
        description=definition.description,
        args_schema=schema,
        coroutine=call,
        response_format="content_and_artifact",
        metadata=definition.langchain_metadata(),
    )


def jsonable(value: Any) -> Any:
    """SDK values as plain JSON (camelCase field names, as on the wire)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return jsonable(model_dump(by_alias=True, exclude_none=True, mode="json"))
    if hasattr(value, "__dict__"):
        return jsonable(vars(value))
    return str(value)


__all__ = [
    "RESULT_ENVELOPE",
    "CallResult",
    "GatewayRefused",
    "GatewayScope",
    "GatewayUnavailable",
    "HttpxBridge",
    "ServerInfo",
    "ToolDefinition",
    "as_langchain_tool",
    "call_tool",
    "discover",
    "http_client_factory",
    "http_transport",
    "jsonable",
    "read_resource",
    "runtime_transport",
    "session",
    "stdio_transport",
]
