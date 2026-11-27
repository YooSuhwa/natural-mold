"""Deep Research search contract with organization-authorized provider failover."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from app.schemas.research_search import SearchProvider
from app.services import research_search_policy
from app.tools.definitions.search_transport import (
    ResearchSearchError,
    SearchUnavailable,
    provider_post,
)
from app.tools.definitions.tavily_search import fetch_tavily, hosted_api_key
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind
from app.tools.risk import ToolRiskLevel

logger = logging.getLogger(__name__)
_RANGES = {"day": 1, "week": 7, "month": 30, "year": 365}


@dataclass(frozen=True)
class SearchRequest:
    query: str
    max_results: int
    time_range: str

    @classmethod
    def from_parameters(cls, params: dict[str, Any]) -> SearchRequest:
        value = params.get("query")
        if not isinstance(value, str):
            raise ResearchSearchError("A search query is required.")
        query = " ".join(value.split())
        if not query or len(query) > 1000:
            raise ResearchSearchError("Search queries must contain 1 to 1000 characters.")
        limit = params.get("max_results", 5)
        if isinstance(limit, float) and limit.is_integer():
            limit = int(limit)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ResearchSearchError("max_results must be an integer between 1 and 10.")
        period = params.get("time_range", "")
        if not isinstance(period, str) or (period and period not in _RANGES):
            raise ResearchSearchError("Use day, week, month, year, or an empty search period.")
        return cls(query=query, max_results=limit, time_range=period)


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None


def _source_url(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(c) <= 32 or c.isspace() or c == "\\" for c in value)
    ):
        return None
    try:
        url = urlsplit(value)
        _ = url.port  # Reject malformed ports.
        if url.scheme in {"http", "https"} and url.hostname and url.username is None:
            return value
    except ValueError:
        pass
    return None


def _normalized_results(
    items: list[Any], request: SearchRequest, provider: SearchProvider
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    earliest = now - timedelta(days=_RANGES[request.time_range]) if request.time_range else None
    results = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _source_url(item.get("url"))
        if not url or url in seen:
            continue
        published = _date(
            item.get("published_date") if provider == "tavily" else item.get("publishedDate")
        )
        # SearXNG backends may ignore time_range. Undated or out-of-range
        # results cannot substantiate a time-qualified citation.
        if earliest and (published is None or not earliest <= published <= now):
            continue
        seen.add(url)
        title = item.get("title")
        content = item.get("content")
        results.append(
            {
                "title": title[:500] if isinstance(title, str) else "",
                "url": url,
                "content": content[:6000] if isinstance(content, str) else "",
                "published_date": published.isoformat() if published else None,
                "provider": provider,
            }
        )
        if len(results) == request.max_results:
            break
    return results


async def _search(
    ctx: ToolRunContext, request: SearchRequest, provider: SearchProvider
) -> list[dict[str, Any]]:
    key = hosted_api_key() if provider == "tavily" else ""
    endpoint = await research_search_policy.reserve_attempt(ctx.search_actor, provider)
    period = {"time_range": request.time_range} if request.time_range else {}
    if provider == "tavily":
        data = await fetch_tavily(
            ctx.http_client,
            key,
            {
                "query": request.query,
                "max_results": request.max_results,
                "include_published_date": True,
                "filter_by_published_date": bool(period),
                **period,
            },
        )
    else:
        if not endpoint:
            raise ResearchSearchError("The approved search provider is not configured.")
        data = await provider_post(
            ctx.http_client,
            endpoint,
            data={
                "q": request.query,
                "format": "json",
                **period,
            },
        )
        if not data["results"] and data.get("unresponsive_engines"):
            # Different engines may report authorization, quota, or connection failures
            # inside HTTP 200. Only recognized transient failures permit another request.
            errors = data["unresponsive_engines"]
            if isinstance(errors, list) and all(
                isinstance(row, list)
                and len(row) >= 2
                and isinstance(row[1], str)
                and row[1].casefold() in {"timeout", "http timeout", "connection error"}
                for row in errors
            ):
                raise SearchUnavailable("Research search is temporarily unavailable. Retry later.")
            raise ResearchSearchError(
                "Search engines did not complete the request. Check provider settings."
            )
    return _normalized_results(data["results"], request, provider)


async def run_research_search(ctx: ToolRunContext) -> dict[str, Any]:
    request = SearchRequest.from_parameters(ctx.parameters)
    providers = await research_search_policy.load_plan(ctx.search_actor)
    last_error: SearchUnavailable | None = None
    for provider in providers:
        if provider == "searxng" and request.time_range == "week":
            continue
        started = monotonic()
        count = 0
        try:
            results = await _search(ctx, request, provider)
            count = len(results)
            return {
                "query": request.query,
                "provider": provider,
                "time_range": request.time_range,
                "results": results,
            }
        except SearchUnavailable as error:
            last_error = error
        finally:
            logger.info(
                "research_search provider=%s duration_ms=%d results=%d",
                provider,
                int((monotonic() - started) * 1000),
                count,
            )
    if last_error:
        raise last_error
    raise ResearchSearchError("No approved search provider supports the requested period.")


definition = ToolDefinition(
    key="research_search",
    display_name="Research Search",
    description="Search approved providers and cite the original result URLs.",
    category="search",
    icon_id="search",
    risk_level=ToolRiskLevel.READ_ONLY,
    trigger_safe=True,
    parameters=[
        FieldDef(
            name="query",
            display_name="Query",
            kind=FieldKind.STRING,
            required=True,
            runtime_only=True,
        ),
        FieldDef(
            name="max_results",
            display_name="Maximum results",
            kind=FieldKind.NUMBER,
            default=5,
            runtime_only=True,
        ),
        FieldDef(
            name="time_range",
            display_name="Search period",
            kind=FieldKind.STRING,
            default="",
            description="day, week, month, year, or empty for any time.",
            runtime_only=True,
        ),
    ],
    runner=run_research_search,
)
