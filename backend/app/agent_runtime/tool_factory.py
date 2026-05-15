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

import logging
import time
import uuid as _uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from langchain_core.tools import BaseTool, StructuredTool

from app.config import settings
from app.hooks import HookContext, HookResult, hooks
from app.tools.domain import ToolRunContext
from app.tools.registry import registry as tool_registry

logger = logging.getLogger(__name__)


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
            async with httpx.AsyncClient(
                timeout=settings.tool_call_timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Moldy Agent Builder)"},
            ) as client:
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
        """현재 날짜와 시간을 반환합니다."""

        now = datetime.now(ZoneInfo("Asia/Seoul"))
        return now.strftime("%Y년 %m월 %d일 %A %H:%M:%S (KST)")

    return StructuredTool.from_function(
        coroutine=get_current_datetime,
        name="current_datetime",
        description="현재 날짜와 시간을 반환합니다. 오늘 날짜, 현재 시간, 요일을 알려줍니다.",
    )


_BUILTIN_BUILDERS: dict[str, Callable[[], BaseTool]] = {
    "builtin:web_search": _build_web_search_tool,
    "builtin:web_scraper": _build_web_scraper_tool,
    "builtin:current_datetime": _build_current_datetime_tool,
}


def create_builtin_tool(definition_key: str) -> BaseTool | None:
    """Return a built-in helper tool if ``definition_key`` matches one."""

    builder = _BUILTIN_BUILDERS.get(definition_key)
    if builder is None:
        return None
    return builder()


# ---------------------------------------------------------------------------
# Registry-backed tools
# ---------------------------------------------------------------------------


def _safe_tool_name(raw_name: str) -> str:
    """LangChain tool names allow ``[a-zA-Z0-9_-]``; coerce display names."""

    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw_name)
    cleaned = cleaned.strip("_") or "tool"
    return cleaned[:60]


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
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
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

    safe_name = _safe_tool_name(tool_config.get("name") or definition.display_name)
    description = (
        tool_config.get("description")
        or definition.description
        or f"Tool {definition.display_name}"
    )

    return StructuredTool.from_function(
        coroutine=_invoke,
        name=safe_name,
        description=description,
    )


__all__ = ["create_builtin_tool", "create_tool_for_runtime"]
