from __future__ import annotations

import html
import re
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.config import settings


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags and unescape HTML entities from Naver API response."""
    cleaned = re.sub(r"<[^>]+>", "", text)
    return html.unescape(cleaned)


def _parse_naver_response(data: dict[str, Any]) -> str:
    """Parse Naver Search API JSON response into a readable string."""
    items = data.get("items", [])
    if not items:
        return "검색 결과가 없습니다."

    results: list[str] = []
    for i, item in enumerate(items, 1):
        parts = [f"[{i}]"]
        if "title" in item:
            parts.append(f"제목: {_strip_html_tags(item['title'])}")
        if "description" in item:
            desc = _strip_html_tags(item["description"])
            if desc:
                parts.append(f"설명: {desc}")
        if "link" in item:
            parts.append(f"링크: {item['link']}")
        if "bloggername" in item:
            parts.append(f"블로거: {item['bloggername']}")
        if "postdate" in item:
            parts.append(f"날짜: {item['postdate']}")
        if "pubDate" in item and "postdate" not in item:
            parts.append(f"날짜: {item['pubDate']}")
        if "category" in item:
            parts.append(f"카테고리: {item['category']}")
        if "address" in item:
            parts.append(f"주소: {item['address']}")
        if "telephone" in item and item["telephone"]:
            parts.append(f"전화: {item['telephone']}")
        if "lprice" in item:
            parts.append(f"최저가: {item['lprice']}원")
        if "hprice" in item and item["hprice"]:
            parts.append(f"최고가: {item['hprice']}원")
        if "mallName" in item:
            parts.append(f"쇼핑몰: {item['mallName']}")
        results.append("\n".join(parts))

    total = data.get("total", len(items))
    header = f"총 {total}건 중 {len(items)}건 표시\n"
    return header + "\n\n".join(results)


# ---------------------------------------------------------------------------
# Pydantic args schemas for each Naver search type
# ---------------------------------------------------------------------------


class _NaverSearchArgsBase(BaseModel):
    query: str = Field(description="검색 키워드")
    display: int = Field(default=10, description="결과 수 (1-100)", ge=1, le=100)
    start: int = Field(default=1, description="시작 위치 (1-1000)", ge=1, le=1000)
    sort: str = Field(default="sim", description="정렬: sim(정확도순), date(날짜순)")


class NaverImageSearchArgs(_NaverSearchArgsBase):
    filter: str = Field(default="all", description="이미지 크기: all/large/medium/small")


class NaverShoppingSearchArgs(_NaverSearchArgsBase):
    sort: str = Field(
        default="sim",
        description="정렬: sim(정확도순), asc(가격낮은순), dsc(가격높은순)",
    )


class NaverLocalSearchArgs(_NaverSearchArgsBase):
    display: int = Field(default=5, description="결과 수 (1-5)", ge=1, le=5)
    start: int = Field(default=1, description="시작 위치 (1-1)", ge=1, le=1)
    sort: str = Field(default="random", description="정렬: random(기본), comment(리뷰순)")


_ARGS_SCHEMAS: dict[str, type[BaseModel]] = {
    "blog": _NaverSearchArgsBase,
    "news": _NaverSearchArgsBase,
    "image": NaverImageSearchArgs,
    "shop": NaverShoppingSearchArgs,
    "local": NaverLocalSearchArgs,
}


# ---------------------------------------------------------------------------
# Generic Naver Search tool builder
# ---------------------------------------------------------------------------


def build_naver_search_tool(
    search_type: str,
    tool_name: str,
    description: str,
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool for a Naver Search API endpoint."""
    args_schema = _ARGS_SCHEMAS[search_type]

    async def _search(**kwargs: Any) -> str:
        client_id = (auth_config or {}).get("naver_client_id") or settings.naver_client_id
        client_secret = (auth_config or {}).get(
            "naver_client_secret"
        ) or settings.naver_client_secret

        if not client_id or not client_secret:
            return (
                "Error: NAVER_CLIENT_ID/SECRET이 설정되지 않았습니다. "
                ".env 파일에 설정하거나 도구의 auth_config에 추가하세요."
            )

        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }

        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"https://openapi.naver.com/v1/search/{search_type}.json",
                    headers=headers,
                    params=kwargs,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return "Error: 네이버 API 일일 호출 한도(25,000건)를 초과했습니다."
            return (
                f"Error: 네이버 API 호출 실패 — {e.response.status_code}: {e.response.text[:200]}"
            )
        except httpx.HTTPError as e:
            return f"Error: 네이버 API에 연결할 수 없습니다 — {e}"

        return _parse_naver_response(resp.json())

    return StructuredTool.from_function(
        coroutine=_search,
        name=tool_name,
        description=description,
        args_schema=args_schema,
    )
