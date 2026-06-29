"""Build LangChain tools for the chat runtime.

Single-path factory: every entry in ``tools_config`` carries a
``definition_key`` from the central :mod:`app.tools.registry` plus an optional
decrypted ``credentials`` dict and the user-supplied ``parameters``. We wrap
the registry runner in a LangChain :class:`StructuredTool` so the agent can
call it just like any other tool.

A few zero-credential utility tools are still bundled here (``Web Search``,
``Web Scraper``, ``Current DateTime``) because they're handy for new agents
out of the box and don't justify a registry entry of their own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid as _uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from app.agent_runtime.temporal import (
    DEFAULT_TIMEZONE,
    build_temporal_context,
    parse_reference_datetime,
    resolve_relative_date_expression,
)
from app.config import settings
from app.hooks import HookContext, HookResult, hooks
from app.http_ssl import get_outbound_ssl_context
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldKind
from app.tools.registry import registry as tool_registry
from app.tools.risk import attach_tool_risk, builtin_tool_risk, risk_from_definition

_KIND_TO_PY_TYPE: dict[FieldKind, type] = {
    FieldKind.STRING: str,
    FieldKind.PASSWORD: str,
    FieldKind.MULTILINE: str,
    FieldKind.SELECT: str,
    FieldKind.OAUTH_BUTTON: str,
    FieldKind.NUMBER: float,
    FieldKind.TOGGLE: bool,
    FieldKind.JSON: dict,
    FieldKind.COLLECTION: dict,
}


def _build_runtime_args_schema(
    definition: ToolDefinition,
    stored_params: dict[str, Any],
) -> type[BaseModel] | None:
    """Build a Pydantic args schema for the LangChain StructuredTool.

    Exposes every ``runtime_only`` field so the LLM can supply per-call
    arguments (e.g. the search ``query``). Operator-pinned values in
    ``stored_params`` become the field default, letting the model omit them
    while still being able to override.
    """

    runtime_fields = [f for f in definition.parameters if f.runtime_only]
    if not runtime_fields:
        return None

    field_specs: dict[str, Any] = {}
    for spec in runtime_fields:
        py_type = _KIND_TO_PY_TYPE.get(spec.kind, str)
        default_value = stored_params.get(spec.name)
        if default_value is None:
            default_value = spec.default
        description = spec.description or spec.display_name
        if spec.required and default_value is None:
            field_specs[spec.name] = (py_type, Field(..., description=description))
        else:
            field_specs[spec.name] = (
                py_type,
                Field(default=default_value, description=description),
            )

    model_name = "".join(ch if ch.isalnum() else "_" for ch in f"{definition.key}_args")
    return create_model(model_name, **field_specs)


logger = logging.getLogger(__name__)

_TOOL_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_tool_http_client() -> httpx.AsyncClient:
    """Return the shared outbound client used by runtime tools."""

    global _TOOL_HTTP_CLIENT
    if _TOOL_HTTP_CLIENT is None or getattr(_TOOL_HTTP_CLIENT, "is_closed", False) is True:
        _TOOL_HTTP_CLIENT = httpx.AsyncClient(
            timeout=settings.tool_call_timeout,
            verify=get_outbound_ssl_context(),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Moldy Agent Builder)"},
        )
    return _TOOL_HTTP_CLIENT


async def close_tool_http_client() -> None:
    """Close and clear the shared runtime-tool HTTP client."""

    global _TOOL_HTTP_CLIENT
    client = _TOOL_HTTP_CLIENT
    _TOOL_HTTP_CLIENT = None
    if client is not None and getattr(client, "is_closed", False) is not True:
        await client.aclose()


def reset_tool_http_client_for_tests() -> None:
    """Clear the cached client without awaiting close; test-only helper."""

    global _TOOL_HTTP_CLIENT
    _TOOL_HTTP_CLIENT = None


# ---------------------------------------------------------------------------
# Built-in helpers (no API key required)
# ---------------------------------------------------------------------------


def _build_web_search_tool() -> BaseTool:
    from langchain_community.tools import DuckDuckGoSearchResults

    return DuckDuckGoSearchResults(
        name="web_search",
        description=(
            "웹에서 키워드를 검색하여 최신 뉴스와 정보를 찾습니다. "
            "각 결과에 title, snippet, link(URL), date, source가 포함됩니다. "
            "반드시 결과의 link를 출처로 사용하세요. URL을 직접 만들지 마세요."
        ),
        num_results=5,
        backend="news",
    )


def _build_web_scraper_tool() -> BaseTool:
    async def scrape_url(url: str) -> str:
        """웹 페이지의 텍스트 내용을 가져옵니다."""

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return "Error: beautifulsoup4 패키지가 설치되지 않았습니다."

        try:
            client = get_tool_http_client()
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Error: 페이지를 가져올 수 없습니다 — {exc}"

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"
        return text

    return StructuredTool.from_function(
        coroutine=scrape_url,
        name="web_scraper",
        description=(
            "웹 페이지의 텍스트 내용을 가져옵니다. "
            "URL을 입력하면 해당 페이지의 주요 텍스트를 추출합니다."
        ),
    )


def _build_current_datetime_tool() -> BaseTool:
    async def get_current_datetime() -> str:
        """현재 날짜와 시간, 이번 주말 등 기준 날짜 컨텍스트를 반환합니다."""

        return json.dumps(build_temporal_context(), ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=get_current_datetime,
        name="current_datetime",
        description=(
            "현재 날짜/시간과 요일, 이번 주말/다음 주 날짜 범위를 JSON으로 반환합니다. "
            "오늘, 요일, 현재 시간 확인이 필요할 때 사용하세요."
        ),
    )


def _build_resolve_relative_date_tool() -> BaseTool:
    async def resolve_relative_date(
        expression: str,
        reference_date: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> str:
        """상대 날짜 표현을 ISO 날짜 범위로 변환합니다."""

        now = parse_reference_datetime(reference_date, timezone=timezone)
        result = resolve_relative_date_expression(
            expression,
            now=now,
            timezone=timezone,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=resolve_relative_date,
        name="resolve_relative_date",
        description=(
            "한국어 상대 날짜 표현을 ISO 날짜 범위로 변환합니다. "
            "예: '이번주 주말', '다음주 수요일', '최근 뉴스', '내일'. "
            "날씨, 뉴스, 일정, 예약 조회 전에 날짜 범위를 확정할 때 사용하세요."
        ),
    )


# E2E-only scripted search tool. Deterministic, no network. The frontend
# search-group aggregate (domain badges + "출처 N개") reads each grouped
# tool-call's ``result``; this tool returns ``{"results":[{title,url}, ...]}``
# whose URLs span multiple domains so the aggregate has real sources to show.
# Each distinct ``query`` returns a DISTINCT slice of sources, so N consecutive
# calls with different queries accumulate to a known unique-source count.
#
# Source plan (9 unique URLs across 5 unique domains):
#   slice 0: react.dev, vercel.com, nextjs.org
#   slice 1: react.dev, typescriptlang.org, developer.mozilla.org
#   slice 2: vercel.com, nextjs.org, typescriptlang.org
# Unique URLs = 9 (every path differs), unique domains = 5.
# Keyed by query so the result is independent of call ordering/concurrency
# (LangGraph's ToolNode may run async tool calls concurrently). The scripted
# model emits exactly these three queries.
E2E_SCRIPTED_SEARCH_TOOL_NAME = "tavily_search"
_E2E_SCRIPTED_SEARCH_RESULTS: dict[str, tuple[dict[str, str], ...]] = {
    "react routing": (
        {"title": "React docs", "url": "https://react.dev/learn"},
        {"title": "Vercel platform", "url": "https://vercel.com/docs"},
        {"title": "Next.js App Router", "url": "https://nextjs.org/docs/app"},
    ),
    "react hooks": (
        {"title": "React hooks", "url": "https://react.dev/reference/react"},
        {"title": "TypeScript handbook", "url": "https://www.typescriptlang.org/docs/"},
        {"title": "MDN fetch", "url": "https://developer.mozilla.org/en-US/docs/Web/API/fetch"},
    ),
    "typescript generics": (
        {"title": "Vercel functions", "url": "https://vercel.com/docs/functions"},
        {
            "title": "Next.js routing",
            "url": "https://nextjs.org/docs/app/building-your-application",
        },
        {
            "title": "TypeScript generics",
            "url": "https://www.typescriptlang.org/docs/handbook/2/generics.html",
        },
    ),
}
# Domain pool for queries that aren't one of the curated keys above. Lets any
# E2E fixture (e.g. a second search group with fresh queries) get a deterministic
# multi-domain result without adding a curated entry. Curated keys keep their
# exact slices so the E2E_SEARCH_GROUP source math (9 URLs / 5 domains) is stable.
_E2E_SCRIPTED_SEARCH_DOMAIN_POOL: tuple[str, ...] = (
    "react.dev",
    "vuejs.org",
    "svelte.dev",
    "nextjs.org",
    "remix.run",
    "angular.dev",
    "solidjs.com",
    "astro.build",
)


def _e2e_scripted_search_slice(query: str) -> list[dict[str, str]]:
    """Curated slice for known queries, else deterministic multi-domain results."""

    curated = _E2E_SCRIPTED_SEARCH_RESULTS.get(query.strip())
    if curated is not None:
        return [dict(item) for item in curated]
    # Deterministically pick 3 distinct domains from the pool by hashing the query
    # (stable md5, not builtin hash() which is PYTHONHASHSEED-randomized), so each
    # distinct query yields a distinct, run-stable multi-domain result (no network).
    pool = _E2E_SCRIPTED_SEARCH_DOMAIN_POOL
    digest = hashlib.md5(query.strip().encode("utf-8")).hexdigest()  # noqa: S324 (non-crypto)
    start = int(digest, 16) % len(pool)
    slug = re.sub(r"[^a-z0-9]+", "-", query.strip().lower()).strip("-") or "q"
    results: list[dict[str, str]] = []
    for offset in range(3):
        domain = pool[(start + offset) % len(pool)]
        results.append({"title": f"{domain} — {query}", "url": f"https://{domain}/{slug}/{offset}"})
    return results


def _build_e2e_scripted_search_tool() -> BaseTool:
    async def tavily_search(query: str) -> str:
        """Deterministic E2E search: returns scripted multi-domain results."""

        results = _e2e_scripted_search_slice(query)
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=tavily_search,
        name=E2E_SCRIPTED_SEARCH_TOOL_NAME,
        description="E2E-only scripted web search returning deterministic multi-domain results.",
    )


# E2E-only generative-UI demo tool. Returns a JSON result carrying ``ui_type``
# so ``ui_data_from_tool_result`` projects it into a ``moldy.ui_data`` event.
# Parameterized by ``kind`` so each component type (Phase 1 demo_note, Phase 2
# data_table/chart/stats/terminal) has a deterministic fixture. Registered only
# when the scripted model is enabled (see ``runtime_component_builder``), so real
# deployments never see it.
E2E_UI_DATA_DEMO_TOOL_NAME = "e2e_ui_data_demo"
E2E_UI_DATA_DEMO_TEXT = "E2E generative UI demo note."
E2E_UI_DATA_DEMO_FIXTURES: dict[str, dict[str, Any]] = {
    "demo_note": {"ui_type": "demo_note", "text": E2E_UI_DATA_DEMO_TEXT},
    "data_table": {
        "ui_type": "data_table",
        "title": "E2E 데이터 테이블",
        "searchable": True,
        "columns": [
            {"key": "name", "header": "이름"},
            {"key": "role", "header": "역할"},
            {"key": "score", "header": "점수"},
        ],
        "rows": [
            {"name": "Alice", "role": "Engineer", "score": 92},
            {"name": "Bob", "role": "Designer", "score": 88},
            {"name": "Carol", "role": "PM", "score": 95},
        ],
    },
    "chart": {
        "ui_type": "chart",
        "chartType": "bar",
        "title": "E2E 주간 차트",
        "yLabel": "건수",
        "series": [
            {"label": "Mon", "value": 12},
            {"label": "Tue", "value": 19},
            {"label": "Wed", "value": 7},
            {"label": "Thu", "value": 22},
            {"label": "Fri", "value": 15},
        ],
    },
    "stats": {
        "ui_type": "stats",
        "items": [
            {"label": "총 요청", "value": 1240, "delta": 12},
            {"label": "성공률", "value": 98.6, "unit": "%", "delta": 2},
            {"label": "평균 지연", "value": 320, "unit": "ms", "delta": -8},
        ],
    },
    "terminal": {
        "ui_type": "terminal",
        "command": "pytest -q",
        "exitCode": 0,
        "lines": [
            "============ test session starts ============",
            "collected 3 items",
            "tests/test_e2e.py ...                   [100%]",
            "============= 3 passed in 0.42s =============",
        ],
    },
}


def _build_e2e_ui_data_demo_tool() -> BaseTool:
    async def e2e_ui_data_demo(kind: str = "demo_note") -> str:
        """Deterministic E2E generative-UI demo: returns a payload for ``kind``."""

        payload = E2E_UI_DATA_DEMO_FIXTURES.get(kind, E2E_UI_DATA_DEMO_FIXTURES["demo_note"])
        return json.dumps(payload, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=e2e_ui_data_demo,
        name=E2E_UI_DATA_DEMO_TOOL_NAME,
        description="E2E-only generative UI demo tool returning a typed ui_data payload by kind.",
    )


_BUILTIN_BUILDERS: dict[str, Callable[[], BaseTool]] = {
    "builtin:web_search": _build_web_search_tool,
    "builtin:web_scraper": _build_web_scraper_tool,
    "builtin:current_datetime": _build_current_datetime_tool,
    "builtin:resolve_relative_date": _build_resolve_relative_date_tool,
    "builtin:e2e_scripted_search": _build_e2e_scripted_search_tool,
    "builtin:e2e_ui_data_demo": _build_e2e_ui_data_demo_tool,
}


def create_builtin_tool(definition_key: str) -> BaseTool | None:
    """Return a built-in helper tool if ``definition_key`` matches one."""

    builder = _BUILTIN_BUILDERS.get(definition_key)
    if builder is None:
        return None
    return attach_tool_risk(builder(), builtin_tool_risk(definition_key))


# ---------------------------------------------------------------------------
# Registry-backed tools
# ---------------------------------------------------------------------------


def _safe_tool_name(raw_name: str, *, fallback: str = "tool") -> str:
    """Coerce user-facing names into provider-safe function names.

    Vertex/Gemini rejects non-ASCII function names and names that start
    with a digit. Keep DB display names untouched; only sanitize the
    runtime tool name sent to the model.
    """

    def normalize(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value)
        return cleaned.strip("_")

    cleaned = normalize(raw_name)
    if not cleaned:
        cleaned = normalize(fallback) or "tool"
    if not re.match(r"^[A-Za-z_]", cleaned):
        cleaned = f"_{cleaned}"
    return cleaned[:128]


def _safe_uuid(value: Any) -> _uuid.UUID | None:
    """Best-effort coercion of optional correlation IDs to ``UUID``."""

    if value is None or value == "":
        return None
    if isinstance(value, _uuid.UUID):
        return value
    try:
        return _uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _summarize_output(output: Any) -> str | None:
    """Cap tool output to the first 200 chars for the audit trail."""

    if output is None:
        return None
    text = output if isinstance(output, str) else str(output)
    return text[:200]


def _build_tool_hook_context(
    *,
    user_uuid: _uuid.UUID | None,
    tool_uuid: _uuid.UUID | None,
    credential_uuid: _uuid.UUID | None,
    agent_uuid: _uuid.UUID | None,
    tool_name: str,
    definition_key: str,
) -> HookContext | None:
    """Build a ``HookContext`` for a tool call. Returns ``None`` without user."""

    if user_uuid is None:
        return None
    return HookContext(
        request_id=str(_uuid.uuid4()),
        kind="tool_call",
        user_id=user_uuid,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        agent_id=agent_uuid,
        tool_id=tool_uuid,
        credential_id=credential_uuid,
        metadata={"tool_name": tool_name, "definition_key": definition_key},
    )


def create_tool_for_runtime(tool_config: dict[str, Any]) -> BaseTool | None:
    """Translate a chat_service ``tools_config`` entry into a LangChain tool.

    ``tool_config`` shape (set by ``app.services.chat_service.build_tools_config``):
    ``{"tool_id", "definition_key", "name", "description", "parameters",
        "credentials", "credential_id", "user_id", "agent_id"}``.

    Credential resolution policy (multi-user, ADR-016 §4):

    - The ``credentials`` dict is pre-resolved by ``build_tools_config`` from
      a ``Tool.credential_id`` that's already owner-checked against the
      caller's user id. We deliberately do **not** consult system credentials
      from inside the tool factory — system credentials are operator-billed
      and only surface through dedicated flows (Fix Agent, image generation,
      builder) where the caller has been explicitly authorized as a
      super_user. A regular user whose tool requires an API key but has no
      personal credential gets a clear ``ToolConfigError`` (raised by the
      runner) with guidance to register one at ``/credentials``.

    Returns ``None`` when the definition is unknown so the caller can warn
    instead of crashing the chat session.
    """

    definition_key = tool_config.get("definition_key", "")

    builtin = create_builtin_tool(definition_key)
    if builtin is not None:
        return builtin

    definition = tool_registry.get(definition_key)
    if definition is None or definition.runner is None:
        logger.warning(
            "skip tool '%s': unknown definition_key '%s'",
            tool_config.get("name"),
            definition_key,
        )
        return None

    stored_params = dict(tool_config.get("parameters") or {})
    credentials = tool_config.get("credentials")

    runner = definition.runner

    # Resolve correlation IDs once — used by every invocation of this tool.
    tool_uuid = _safe_uuid(tool_config.get("tool_id"))
    credential_uuid = _safe_uuid(tool_config.get("credential_id"))
    user_uuid = _safe_uuid(tool_config.get("user_id"))
    agent_uuid = _safe_uuid(tool_config.get("agent_id"))
    tool_display_name = tool_config.get("name") or definition.display_name

    async def _invoke(**runtime_args: Any) -> Any:
        merged = {**stored_params, **runtime_args}
        hook_ctx = _build_tool_hook_context(
            user_uuid=user_uuid,
            tool_uuid=tool_uuid,
            credential_uuid=credential_uuid,
            agent_uuid=agent_uuid,
            tool_name=tool_display_name,
            definition_key=definition_key,
        )
        if hook_ctx is not None:
            await hooks.run_pre(hook_ctx)
        started = time.monotonic()
        try:
            client = get_tool_http_client()
            run_ctx = ToolRunContext(
                parameters=merged,
                credentials=credentials,
                http_client=client,
            )
            output = await runner(run_ctx)
        except Exception as exc:
            if hook_ctx is not None:
                await hooks.run_failure(hook_ctx, exc)
            raise
        if hook_ctx is not None:
            await hooks.run_post(
                hook_ctx,
                HookResult(
                    duration_ms=int((time.monotonic() - started) * 1000),
                    output=_summarize_output(output),
                ),
            )
        return output

    safe_name = _safe_tool_name(
        tool_config.get("name") or definition.display_name,
        fallback=definition_key,
    )
    description = (
        tool_config.get("description")
        or definition.description
        or f"Tool {definition.display_name}"
    )

    args_schema = _build_runtime_args_schema(definition, stored_params)
    tool = StructuredTool.from_function(
        coroutine=_invoke,
        name=safe_name,
        description=description,
        args_schema=args_schema,
    )
    return attach_tool_risk(tool, risk_from_definition(definition))


__all__ = [
    "close_tool_http_client",
    "create_builtin_tool",
    "create_tool_for_runtime",
    "get_tool_http_client",
]
