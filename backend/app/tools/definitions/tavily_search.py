"""Tavily hosted web search tool.

The API key is owned by the backend operator, not by individual users. This
lets system skills depend on live web search without asking users to bind a
credential for every installed skill.
"""

from __future__ import annotations

import os
from typing import Any

from app.config import settings
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind
from app.tools.risk import ToolRiskLevel


def _string_param(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _bool_param(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_param(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


async def _run_tavily_search(ctx: ToolRunContext) -> dict[str, Any]:
    api_key = settings.tavily_api_key.strip() or os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise ValueError("TAVILY_API_KEY is not configured on the backend server")

    query = _string_param(ctx.parameters.get("query"))
    if not query:
        raise ValueError("query is required")

    time_range = _string_param(ctx.parameters.get("time_range"))
    payload: dict[str, Any] = {
        "query": query,
        "search_depth": _string_param(ctx.parameters.get("search_depth"), "basic") or "basic",
        "topic": _string_param(ctx.parameters.get("topic"), "general") or "general",
        "max_results": _int_param(
            ctx.parameters.get("max_results"),
            default=5,
            minimum=1,
            maximum=10,
        ),
        "include_answer": _bool_param(ctx.parameters.get("include_answer"), True),
        "include_raw_content": _bool_param(ctx.parameters.get("include_raw_content"), False),
    }
    if time_range:
        payload["time_range"] = time_range

    response = await ctx.http_client.post(
        "https://api.tavily.com/search",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for item in data.get("results") or []:
        raw_content = item.get("raw_content")
        if isinstance(raw_content, str) and len(raw_content) > 6000:
            raw_content = raw_content[:6000] + "\n...[truncated]"
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": item.get("content"),
                "raw_content": raw_content,
                "score": item.get("score"),
                "published_date": item.get("published_date"),
            }
        )

    return {
        "query": data.get("query", query),
        "answer": data.get("answer"),
        "results": results,
        "response_time": data.get("response_time"),
    }


definition = ToolDefinition(
    key="tavily_search",
    display_name="Tavily Search",
    description="Search the live web with Tavily using a backend-hosted API key.",
    icon_id="search",
    category="search",
    risk_level=ToolRiskLevel.READ_ONLY,
    trigger_safe=True,
    credential_definition_keys=[],
    parameters=[
        FieldDef(
            name="query",
            display_name="Query",
            kind=FieldKind.STRING,
            required=True,
            runtime_only=True,
            description="Search query supplied by the agent at runtime.",
        ),
        FieldDef(
            name="max_results",
            display_name="Max Results",
            kind=FieldKind.NUMBER,
            default=5,
            type_options={"min": 1, "max": 10},
        ),
        FieldDef(
            name="search_depth",
            display_name="Search Depth",
            kind=FieldKind.SELECT,
            default="basic",
            options=[
                {"name": "Basic", "value": "basic"},
                {"name": "Advanced", "value": "advanced"},
            ],
        ),
        FieldDef(
            name="topic",
            display_name="Topic",
            kind=FieldKind.SELECT,
            default="general",
            options=[
                {"name": "General", "value": "general"},
                {"name": "News", "value": "news"},
                {"name": "Finance", "value": "finance"},
            ],
        ),
        FieldDef(
            name="time_range",
            display_name="Time Range",
            kind=FieldKind.SELECT,
            default="",
            options=[
                {"name": "Any time", "value": ""},
                {"name": "Past day", "value": "day"},
                {"name": "Past week", "value": "week"},
                {"name": "Past month", "value": "month"},
                {"name": "Past year", "value": "year"},
            ],
        ),
        FieldDef(
            name="include_answer",
            display_name="Include Answer",
            kind=FieldKind.TOGGLE,
            default=True,
        ),
        FieldDef(
            name="include_raw_content",
            display_name="Include Raw Content",
            kind=FieldKind.TOGGLE,
            default=False,
        ),
    ],
    runner=_run_tavily_search,
)
