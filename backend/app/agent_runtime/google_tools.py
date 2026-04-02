from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.config import settings

_GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"


def _parse_google_response(data: dict[str, Any]) -> str:
    """Parse Google Custom Search JSON API response into a readable string."""
    items = data.get("items", [])
    if not items:
        return "검색 결과가 없습니다."

    results: list[str] = []
    for i, item in enumerate(items, 1):
        parts = [f"[{i}]"]
        if "title" in item:
            parts.append(f"제목: {item['title']}")
        if "snippet" in item:
            parts.append(f"설명: {item['snippet']}")
        if "link" in item:
            parts.append(f"링크: {item['link']}")
        if "image" in item:
            img = item["image"]
            if "contextLink" in img:
                parts.append(f"출처: {img['contextLink']}")
            if "height" in img and "width" in img:
                parts.append(f"크기: {img['width']}x{img['height']}")
        results.append("\n".join(parts))

    total = data.get("searchInformation", {}).get("totalResults", len(items))
    header = f"총 {total}건 중 {len(items)}건 표시\n"
    return header + "\n\n".join(results)


# ---------------------------------------------------------------------------
# Pydantic args schemas
# ---------------------------------------------------------------------------

class GoogleSearchArgs(BaseModel):
    query: str = Field(description="검색 키워드")
    num: int = Field(default=5, description="결과 수 (1-10)", ge=1, le=10)


_ARGS_SCHEMAS: dict[str, type[BaseModel]] = {
    "web": GoogleSearchArgs,
    "news": GoogleSearchArgs,
    "image": GoogleSearchArgs,
}


# ---------------------------------------------------------------------------
# Generic Google Search tool builder
# ---------------------------------------------------------------------------

def build_google_search_tool(
    variant: str,
    tool_name: str,
    description: str,
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool for Google Custom Search API."""
    args_schema = _ARGS_SCHEMAS[variant]

    async def _search(query: str, num: int = 5) -> str:
        api_key = (auth_config or {}).get("google_api_key") or settings.google_api_key
        cse_id = (auth_config or {}).get("google_cse_id") or settings.google_cse_id

        if not api_key or not cse_id:
            return "Error: GOOGLE_API_KEY/GOOGLE_CSE_ID가 설정되지 않았습니다. .env 파일에 설정하거나 도구의 auth_config에 추가하세요."

        params: dict[str, Any] = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": num,
        }
        if variant == "news":
            params["tbm"] = "nws"
        elif variant == "image":
            params["searchType"] = "image"

        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(_GOOGLE_CSE_URL, params=params)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return "Error: Google API 할당량을 초과했거나 API 키가 유효하지 않습니다."
            return f"Error: Google API 호출 실패 — {e.response.status_code}: {e.response.text[:200]}"
        except httpx.HTTPError as e:
            return f"Error: Google API에 연결할 수 없습니다 — {e}"

        return _parse_google_response(resp.json())

    return StructuredTool.from_function(
        coroutine=_search,
        name=tool_name,
        description=description,
        args_schema=args_schema,
    )
