from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

import httpx
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.google_tools import build_google_search_tool
from app.agent_runtime.google_workspace_tools import (
    build_calendar_create_event_tool,
    build_calendar_list_events_tool,
    build_calendar_update_event_tool,
    build_gmail_read_tool,
    build_gmail_send_tool,
    build_google_chat_webhook_tool,
)
from app.agent_runtime.naver_tools import build_naver_search_tool
from app.config import settings

# ---------------------------------------------------------------------------
# Builtin tool implementations — no API key required
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
        except httpx.HTTPError as e:
            return f"Error: 페이지를 가져올 수 없습니다 — {e}"

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Limit to ~4000 chars to avoid overwhelming the LLM
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


# ---------------------------------------------------------------------------
# HTTP-based custom tools — user-defined via UI
# ---------------------------------------------------------------------------


def _build_http_tool_func(
    api_url: str,
    http_method: str,
    auth_type: str | None,
    auth_config: dict[str, Any] | None,
) -> Any:
    async def call_api(**kwargs: Any) -> str:
        headers: dict[str, str] = {}
        if auth_type == "api_key" and auth_config:
            header_name = auth_config.get("header_name", "Authorization")
            key = auth_config.get("api_key", "")
            headers[header_name] = key
        elif auth_type == "bearer" and auth_config:
            token = auth_config.get("token", "")
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
            method = http_method.upper()
            if method == "GET":
                resp = await client.get(api_url, params=kwargs, headers=headers)
            elif method == "POST":
                resp = await client.post(api_url, json=kwargs, headers=headers)
            elif method == "PUT":
                resp = await client.put(api_url, json=kwargs, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(api_url, params=kwargs, headers=headers)
            else:
                resp = await client.get(api_url, params=kwargs, headers=headers)

            return resp.text

    return call_api


def create_tool_from_db(
    name: str,
    description: str | None,
    api_url: str,
    http_method: str,
    parameters_schema: dict[str, Any] | None,
    auth_type: str | None,
    auth_config: dict[str, Any] | None,
) -> StructuredTool:
    func = _build_http_tool_func(api_url, http_method, auth_type, auth_config)

    return StructuredTool.from_function(
        coroutine=func,
        name=name,
        description=description or f"Call {name}",
        args_schema=None,  # LangChain will infer from function signature
    )


# ---------------------------------------------------------------------------
# Public API — builtin (no key) and prebuilt (API key required)
# ---------------------------------------------------------------------------

_BUILTIN_BUILDERS: dict[str, Callable[[], BaseTool]] = {
    "Web Search": _build_web_search_tool,
    "Web Scraper": _build_web_scraper_tool,
    "Current DateTime": _build_current_datetime_tool,
}


def create_builtin_tool(name: str) -> BaseTool:
    """Create a LangChain tool for a builtin tool (no API key required)."""
    builder = _BUILTIN_BUILDERS.get(name)
    if not builder:
        raise ValueError(f"Unknown builtin tool: {name}")
    return builder()


class _PrebuiltEntry(NamedTuple):
    provider: str
    search_type: str
    tool_name: str
    description: str


_PREBUILT_REGISTRY: dict[str, _PrebuiltEntry] = {
    "Naver Blog Search": _PrebuiltEntry(
        "naver",
        "blog",
        "naver_blog_search",
        "네이버 블로그에서 키워드를 검색합니다. "
        "블로그 포스트, 리뷰, 개인 의견 등을 찾을 때 사용하세요.",
    ),
    "Naver News Search": _PrebuiltEntry(
        "naver",
        "news",
        "naver_news_search",
        "네이버 뉴스에서 키워드를 검색합니다. 최신 뉴스, 기사, 보도 내용을 찾을 때 사용하세요.",
    ),
    "Naver Image Search": _PrebuiltEntry(
        "naver",
        "image",
        "naver_image_search",
        "네이버에서 이미지를 검색합니다. 사진, 일러스트, 인포그래픽 등을 찾을 때 사용하세요.",
    ),
    "Naver Shopping Search": _PrebuiltEntry(
        "naver",
        "shop",
        "naver_shopping_search",
        "네이버 쇼핑에서 상품을 검색합니다. 가격 비교, 상품 정보 조회에 사용하세요.",
    ),
    "Naver Local Search": _PrebuiltEntry(
        "naver",
        "local",
        "naver_local_search",
        "네이버에서 지역 업체를 검색합니다. 맛집, 카페, 병원 등 주변 업체를 찾을 때 사용하세요.",
    ),
    "Google Search": _PrebuiltEntry(
        "google",
        "web",
        "google_search",
        "구글에서 웹 페이지를 검색합니다. 영문 검색, 글로벌 정보 검색에 특히 유용합니다.",
    ),
    "Google News Search": _PrebuiltEntry(
        "google",
        "news",
        "google_news_search",
        "구글 뉴스에서 키워드를 검색합니다. 글로벌 뉴스, 영문 기사를 찾을 때 사용하세요.",
    ),
    "Google Image Search": _PrebuiltEntry(
        "google",
        "image",
        "google_image_search",
        "구글에서 이미지를 검색합니다. 글로벌 이미지, 영문 키워드 검색에 유용합니다.",
    ),
    "Google Chat Send": _PrebuiltEntry(
        "google_workspace",
        "chat_send",
        "google_chat_send",
        "Google Chat 채널에 메시지를 전송합니다. 알림, 보고, 요약 결과 공유 등에 사용하세요.",
    ),
    "Gmail Read": _PrebuiltEntry(
        "google_workspace",
        "gmail_read",
        "gmail_read",
        "Gmail에서 이메일을 검색하고 읽습니다. 검색 쿼리로 필터링할 수 있습니다.",
    ),
    "Gmail Send": _PrebuiltEntry(
        "google_workspace",
        "gmail_send",
        "gmail_send",
        "Gmail로 이메일을 전송합니다. 수신자, 제목, 본문을 지정하여 이메일을 보냅니다.",
    ),
    "Calendar List Events": _PrebuiltEntry(
        "google_workspace",
        "calendar_list",
        "calendar_list_events",
        "Google Calendar에서 일정을 조회합니다. 오늘 또는 며칠간의 일정을 확인할 수 있습니다.",
    ),
    "Calendar Create Event": _PrebuiltEntry(
        "google_workspace",
        "calendar_create",
        "calendar_create_event",
        "Google Calendar에 새 일정을 생성합니다. "
        "제목, 시작/종료 시간, 설명, 장소를 지정할 수 있습니다.",
    ),
    "Calendar Update Event": _PrebuiltEntry(
        "google_workspace",
        "calendar_update",
        "calendar_update_event",
        "Google Calendar의 기존 일정을 수정합니다. 일정 ID와 변경할 필드를 지정합니다.",
    ),
}


def create_prebuilt_tool(name: str, auth_config: dict[str, Any] | None = None) -> BaseTool:
    """Create a LangChain tool for a prebuilt API tool (API key required)."""
    entry = _PREBUILT_REGISTRY.get(name)
    if not entry:
        raise ValueError(f"Unknown prebuilt tool: {name}")

    provider, search_type, tool_name, description = entry
    if provider == "naver":
        return build_naver_search_tool(search_type, tool_name, description, auth_config)
    elif provider == "google":
        return build_google_search_tool(search_type, tool_name, description, auth_config)
    elif provider == "google_workspace":
        if search_type == "chat_send":
            return build_google_chat_webhook_tool(auth_config)
        elif search_type == "gmail_read":
            return build_gmail_read_tool(auth_config)
        elif search_type == "gmail_send":
            return build_gmail_send_tool(auth_config)
        elif search_type == "calendar_list":
            return build_calendar_list_events_tool(auth_config)
        elif search_type == "calendar_create":
            return build_calendar_create_event_tool(auth_config)
        elif search_type == "calendar_update":
            return build_calendar_update_event_tool(auth_config)
        raise ValueError(f"Unknown google_workspace tool: {search_type}")
    else:
        raise ValueError(f"Unknown prebuilt provider: {provider}")
