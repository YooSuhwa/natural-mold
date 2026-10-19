from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.mcp_app_runtime import (
    ResultMetaInterceptor,
    mcp_app_context,
    record_mcp_app_binding,
)
from app.agent_runtime.mcp_auth_context import McpAuthContext
from app.agent_runtime.mcp_auth_errors import McpAuthError, classify_mcp_error
from app.agent_runtime.mcp_destination import split_blocked, transport_options
from app.agent_runtime.mcp_tool_cache import identity_cache_key
from app.agent_runtime.runtime_hydration_guard import assert_pre_marker
from app.mcp.apps import normalize_tool_ui_meta, tool_allows_visibility
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


def _create_mcp_auth_stub(name: str, error: McpAuthError, config: dict[str, Any]) -> BaseTool:
    """Discovery hit 401/403: calls answer with the typed auth error (F-12)."""
    from app.agent_runtime.mcp_cache import MCPToolWithRetry

    async def _call(**kwargs: Any) -> str:
        raise error

    stub = StructuredTool.from_function(
        coroutine=_call, name=name, description=f"MCP tool (authorization required): {name}"
    )
    wrapped = MCPToolWithRetry(stub, max_retries=1, auth_context=McpAuthContext.from_config(config))
    return attach_tool_risk(wrapped, mcp_tool_risk(name))


def server_interceptors(plan: _McpPlan, key: str) -> list[Any]:
    """Only this server's ``auth_config`` is injected (the cache key covers it)."""
    auth = plan.server_tool_auth.get(key)
    interceptors: list[Any] = [ResultMetaInterceptor()]
    if auth:
        interceptors.insert(0, _AuthInjectorInterceptor(dict(auth)))
    return interceptors


@dataclass(slots=True)
class _McpPlan:
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_filter: dict[str, set[str]] = field(default_factory=dict)
    tool_auth: dict[str, dict] = field(default_factory=dict)
    server_tool_auth: dict[str, dict[str, dict]] = field(default_factory=dict)
    tool_configs: dict[tuple[str, str], dict] = field(default_factory=dict)
    auth_param_keys: set[str] = field(default_factory=set)


def _plan_mcp_servers(mcp_configs: list[dict]) -> _McpPlan:
    plan = _McpPlan()
    # 1. MCP 서버 (URL, transport headers)별로 그룹화 — 같은 URL이어도 다른
    # 연결이 다른 헤더(X-Tenant 등)를 쓸 수 있으므로 URL만으로 묶으면 멀티
    # 테넌트 MCP gateway에서 cross-tenant 헤더 혼선이 발생한다 (Codex 7차
    # adversarial P2). 헤더 조합도 함께 키로 사용해 분리.
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

        if key not in plan.servers:
            plan.servers[key] = {
                "transport": "streamable_http",
                "url": url,
                "headers": headers or None,
                **transport_options(tc),
            }
            plan.tool_filter[key] = set()

        plan.tool_filter[key].add(tool_name)
        plan.tool_configs[(key, tool_name)] = tc

        auth = tc.get("auth_config")
        if auth:
            plan.tool_auth[tool_name] = auth
            plan.server_tool_auth.setdefault(key, {})[tool_name] = auth

    # auth 파라미터 키 수집 (스키마에서 숨길 대상)
    for auth in plan.tool_auth.values():
        plan.auth_param_keys.update(auth.keys())
    return plan


def _wrap_selected_tool(
    t: BaseTool,
    key: str,
    config: dict[str, Any],
    plan: _McpPlan,
    timeout: float,
    *,
    auth_keys: set[str] | None = None,
    max_retries: int = 2,
) -> BaseTool | None:
    """Hide auth params, attach risk/UI metadata and the retry wrapper (``None`` = hidden)."""
    from app.agent_runtime.mcp_cache import MCPToolWithRetry

    _hide_auth_params_from_schema(t, plan.auth_param_keys if auth_keys is None else auth_keys)
    risk_config = dict(plan.tool_configs.get((key, t.name), {}))
    risk_config.setdefault("definition_key", "mcp")
    risk_config.setdefault("name", t.name)
    risk_config.setdefault("mcp_tool_name", t.name)
    risk_config.setdefault("mcp_server_url", config.get("url"))
    if t.description and not risk_config.get("description"):
        risk_config["description"] = t.description
    metadata = getattr(t, "metadata", None)
    raw_meta = metadata.get("_meta") if isinstance(metadata, dict) else None
    tool_ui_meta = normalize_tool_ui_meta(raw_meta)
    if tool_ui_meta is not None and not tool_allows_visibility(tool_ui_meta, "model"):
        return None
    tool_config = plan.tool_configs.get((key, t.name))
    app_context = mcp_app_context(tool_config, tool_ui_meta)
    wrapped = MCPToolWithRetry(
        t,
        max_retries=max_retries,
        retry_delay=0.25,
        timeout_seconds=timeout,
        mcp_app_context=app_context,
        binding_recorder=(record_mcp_app_binding if app_context is not None else None),
        auth_context=McpAuthContext.from_config(tool_config or {}),
    )
    return attach_tool_risk(
        wrapped,
        mcp_tool_risk(
            wrapped.name,
            metadata=metadata if isinstance(metadata, dict) else None,
            config=risk_config,
        ),
    )


def _disambiguate(collected: list[tuple[BaseTool, str]]) -> list[BaseTool]:
    # 3. 중복 이름 disambiguation — 서버 키를 prefix로 추가
    name_counts: dict[str, int] = {}
    for tool, _ in collected:
        name_counts[tool.name] = name_counts.get(tool.name, 0) + 1

    if any(c > 1 for c in name_counts.values()):
        for tool, origin in collected:
            if name_counts.get(tool.name, 0) > 1:
                tool.name = f"{origin}_{tool.name}"

    return [tool for tool, _ in collected]


async def _build_mcp_tools(mcp_configs: list[dict]) -> list[BaseTool]:
    """MCP 도구를 langchain-mcp-adapters로 생성."""
    assert_pre_marker("_build_mcp_tools")
    if not mcp_configs:
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    mcp_configs, blocked = split_blocked(mcp_configs)  # 11.9 credential destinations
    plan = _plan_mcp_servers(mcp_configs)

    # 2. 서버별로 도구 로딩 + 필터링 — (tool, origin) 쌍으로 추적
    collected: list[tuple[BaseTool, str]] = [(stub, "blocked") for stub in blocked]

    from app.agent_runtime.mcp_cache import get_cached_mcp_tools
    from app.config import settings as _settings

    for key, config in plan.servers.items():
        server_configs = [tc for (k, _), tc in plan.tool_configs.items() if k == key]
        identity_key, cache_tags = identity_cache_key(
            key, server_configs, plan.server_tool_auth.get(key, {})
        )
        try:

            async def _load_server_tools(
                *,
                cache_key: str = key,
                server_config: dict[str, Any] = config,
            ) -> list[BaseTool]:
                client = MultiServerMCPClient(
                    {cache_key: server_config},  # type: ignore[arg-type]  # dict는 Connection TypedDict 호환
                    tool_interceptors=server_interceptors(plan, cache_key),  # type: ignore[arg-type]
                )
                return await asyncio.wait_for(
                    client.get_tools(),
                    timeout=_settings.mcp_connection_timeout,
                )

            server_tools = await get_cached_mcp_tools(
                identity_key,
                _load_server_tools,
                ttl_seconds=max(1.0, float(_settings.mcp_connection_timeout) * 30),
                tags=cache_tags,
            )
            needed = plan.tool_filter[key]
            for t in server_tools:
                if t.name in needed:
                    wrapped = _wrap_selected_tool(
                        t, key, config, plan, float(_settings.mcp_connection_timeout)
                    )
                    if wrapped is not None:
                        collected.append((wrapped, key))
        except Exception as exc:
            auth_error = classify_mcp_error(exc)
            if auth_error is None:
                logger.warning("MCP tool loading failed for %s", key, exc_info=True)
            else:
                logger.warning("MCP tool loading for %s refused: %s", key, auth_error.kind)
            for tool_name in plan.tool_filter[key]:
                stub = (
                    _create_mcp_error_stub(tool_name)
                    if auth_error is None
                    else _create_mcp_auth_stub(
                        tool_name, auth_error, plan.tool_configs[(key, tool_name)]
                    )
                )
                collected.append((stub, key))

    return _disambiguate(collected)
