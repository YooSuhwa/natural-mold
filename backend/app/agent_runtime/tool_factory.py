from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from langchain_core.tools import BaseTool, StructuredTool

from app.config import settings


# ---------------------------------------------------------------------------
# Builtin tools — no API keys required, shipped with Moldy
# ---------------------------------------------------------------------------

def create_builtin_tool(name: str) -> BaseTool:
    """Create a LangChain tool for a builtin tool by name."""
    builders: dict[str, Any] = {
        "Web Search": _build_web_search_tool,
        "Web Scraper": _build_web_scraper_tool,
        "Current DateTime": _build_current_datetime_tool,
    }
    builder = builders.get(name)
    if not builder:
        raise ValueError(f"Unknown builtin tool: {name}")
    return builder()


def _build_web_search_tool() -> BaseTool:
    from langchain_community.tools import DuckDuckGoSearchRun

    return DuckDuckGoSearchRun(
        name="web_search",
        description="웹에서 키워드를 검색하여 관련 정보를 찾습니다. 뉴스, 기사, 정보 검색에 사용하세요.",
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
        description="웹 페이지의 텍스트 내용을 가져옵니다. URL을 입력하면 해당 페이지의 주요 텍스트를 추출합니다.",
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
