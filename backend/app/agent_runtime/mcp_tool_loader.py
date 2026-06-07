from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import BaseTool, StructuredTool

from app.tools.risk import attach_tool_risk, mcp_tool_risk

logger = logging.getLogger(__name__)


def _auth_config_to_headers(auth_config: dict[str, str] | None) -> dict[str, str]:
    """auth_config를 HTTP 헤더로 변환."""
    if not auth_config:
        return {}
    if "headers" in auth_config:
        return auth_config["headers"]  # type: ignore[return-value]  # legacy: dict 형태 전달 시
    return {}


def _url_to_server_key(url: str) -> str:
    """MCP 서버 URL을 고유 키로 변환 (호스트 + 경로 포함)."""
    parsed = urlparse(url)
    key = parsed.netloc + parsed.path.rstrip("/")
    return key.replace(".", "_").replace(":", "_").replace("/", "_")


class _AuthInjectorInterceptor:
    """MCP 도구 호출 시 auth_config 값을 arguments에 자동 주입.

    langchain-mcp-adapters의 ToolCallInterceptor 프로토콜을 구현.
    MCP 서버로 JSON-RPC tools/call 전송 직전에 request.args를 수정한다.
    """

    def __init__(self, tool_auth: dict[str, dict]) -> None:
        self.tool_auth = tool_auth  # tool_name → auth_config

    async def __call__(self, request: Any, handler: Any) -> Any:
        auth = self.tool_auth.get(request.name)
        if auth:
            merged = {**auth, **request.args}
            request = request.override(args=merged)
        return await handler(request)


def _hide_auth_params_from_schema(tool: BaseTool, auth_keys: set[str]) -> None:
    """도구의 dict 스키마에서 auth 파라미터를 제거하여 LLM에게 숨김."""
    schema = tool.args_schema
    if not isinstance(schema, dict) or not auth_keys:
        return
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for key in auth_keys:
        props.pop(key, None)
    schema["required"] = [r for r in required if r not in auth_keys]


def _create_mcp_error_stub(name: str) -> BaseTool:
    """MCP 서버 연결 실패 시 에러를 반환하는 stub 도구."""

    async def _call(**kwargs: Any) -> str:
        return f"MCP tool '{name}' is temporarily unavailable. Please try again later."

    tool = StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=f"MCP tool (currently unavailable): {name}",
    )
    return attach_tool_risk(tool, mcp_tool_risk(name))


async def _build_mcp_tools(mcp_configs: list[dict]) -> list[BaseTool]:
    """MCP 도구를 langchain-mcp-adapters로 생성."""
    if not mcp_configs:
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    # 1. MCP 서버 (URL, transport headers)별로 그룹화 — 같은 URL이어도 다른
    # 연결이 다른 헤더(X-Tenant 등)를 쓸 수 있으므로 URL만으로 묶으면 멀티
    # 테넌트 MCP gateway에서 cross-tenant 헤더 혼선이 발생한다 (Codex 7차
    # adversarial P2). 헤더 조합도 함께 키로 사용해 분리.
    servers: dict[str, dict] = {}
    tool_filter: dict[str, set[str]] = {}  # server_key → {tool_names}
    tool_auth: dict[str, dict] = {}  # tool_name → auth_config
    tool_configs: dict[tuple[str, str], dict] = {}  # (server_key, tool_name) → runtime config

    for tc in mcp_configs:
        url = tc["mcp_server_url"]
        tool_name = tc.get("mcp_tool_name", tc["name"])
        # transport 헤더는 `mcp_transport_headers`(신규 경로, connection
        # 경유) 우선 사용. legacy auth_config["headers"]도 fallback.
        headers = tc.get("mcp_transport_headers") or _auth_config_to_headers(tc.get("auth_config"))
        # 정렬된 JSON 직렬화의 SHA256 단축 해시 — process 재시작 후에도 같은
        # (url, headers) 조합이 같은 key/이름 prefix를 생성하도록 deterministic
        # 사용. `hash()`는 PYTHONHASHSEED 때문에 process-randomized라 HiTL
        # resume 시 tool name이 바뀜 (Codex 8차 adversarial F2).
        headers_digest = hashlib.sha256(
            json.dumps(headers or {}, sort_keys=True).encode()
        ).hexdigest()[:8]
        key = f"{_url_to_server_key(url)}|{headers_digest}"

        if key not in servers:
            servers[key] = {
                "transport": "streamable_http",
                "url": url,
                "headers": headers or None,
            }
            tool_filter[key] = set()

        tool_filter[key].add(tool_name)
        tool_configs[(key, tool_name)] = tc

        auth = tc.get("auth_config")
        if auth:
            tool_auth[tool_name] = auth

    # auth 파라미터 키 수집 (스키마에서 숨길 대상)
    auth_param_keys: set[str] = set()
    for auth in tool_auth.values():
        auth_param_keys.update(auth.keys())

    # interceptor: MCP tools/call 직전에 auth 값을 arguments에 주입
    interceptors = [_AuthInjectorInterceptor(tool_auth)] if tool_auth else None

    # 2. 서버별로 도구 로딩 + 필터링 — (tool, origin) 쌍으로 추적
    collected: list[tuple[BaseTool, str]] = []

    from app.agent_runtime.mcp_cache import MCPToolWithRetry, get_cached_mcp_tools
    from app.config import settings as _settings

    for key, config in servers.items():
        try:

            async def _load_server_tools(
                *,
                cache_key: str = key,
                server_config: dict[str, Any] = config,
            ) -> list[BaseTool]:
                client = MultiServerMCPClient(
                    {cache_key: server_config},  # type: ignore[arg-type]  # dict는 Connection TypedDict 호환
                    tool_interceptors=interceptors,  # type: ignore[arg-type]
                )
                return await asyncio.wait_for(
                    client.get_tools(),
                    timeout=_settings.mcp_connection_timeout,
                )

            server_tools = await get_cached_mcp_tools(
                key,
                _load_server_tools,
                ttl_seconds=max(1.0, float(_settings.mcp_connection_timeout) * 30),
            )
            needed = tool_filter[key]
            for t in server_tools:
                if t.name in needed:
                    _hide_auth_params_from_schema(t, auth_param_keys)
                    risk_config = dict(tool_configs.get((key, t.name), {}))
                    risk_config.setdefault("definition_key", "mcp")
                    risk_config.setdefault("name", t.name)
                    risk_config.setdefault("mcp_tool_name", t.name)
                    risk_config.setdefault("mcp_server_url", config.get("url"))
                    if t.description and not risk_config.get("description"):
                        risk_config["description"] = t.description
                    metadata = getattr(t, "metadata", None)
                    wrapped = MCPToolWithRetry(
                        t,
                        max_retries=2,
                        retry_delay=0.25,
                        timeout_seconds=float(_settings.mcp_connection_timeout),
                    )
                    attach_tool_risk(
                        wrapped,
                        mcp_tool_risk(
                            wrapped.name,
                            metadata=metadata if isinstance(metadata, dict) else None,
                            config=risk_config,
                        ),
                    )
                    collected.append((wrapped, key))
        except Exception:
            logger.warning("MCP tool loading failed for %s", key, exc_info=True)
            for tool_name in tool_filter[key]:
                collected.append((_create_mcp_error_stub(tool_name), key))

    # 3. 중복 이름 disambiguation — 서버 키를 prefix로 추가
    name_counts: dict[str, int] = {}
    for tool, _ in collected:
        name_counts[tool.name] = name_counts.get(tool.name, 0) + 1

    if any(c > 1 for c in name_counts.values()):
        for tool, origin in collected:
            if name_counts.get(tool.name, 0) > 1:
                tool.name = f"{origin}_{tool.name}"

    return [tool for tool, _ in collected]
