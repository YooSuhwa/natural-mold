"""Naver Open API — Search.

One :class:`ToolDefinition` per endpoint (blog/news/image/shop/local). All
share a single runner builder; the only differences are the URL path and the
default value ranges. Authentication is delegated to the ``naver_search``
credential definition (header-based).
"""

from __future__ import annotations

import html
import re
from typing import Any

from app.credentials.authenticate import apply_authentication
from app.credentials.registry import registry as credential_registry
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind

_BASE_URL = "https://openapi.naver.com/v1/search"


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _format_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a Naver API ``items`` array — strip HTML tags from text."""

    cleaned: list[dict[str, Any]] = []
    for item in items:
        out: dict[str, Any] = {}
        for key, value in item.items():
            if isinstance(value, str) and key in {"title", "description", "category"}:
                out[key] = _strip_html(value)
            else:
                out[key] = value
        cleaned.append(out)
    return cleaned


def _make_runner(endpoint: str):
    async def _runner(ctx: ToolRunContext) -> dict[str, Any]:
        if ctx.credentials is None:
            raise ValueError("naver_search credential is required")

        params = {
            "query": ctx.parameters["query"],
            "display": int(ctx.parameters.get("display", 10)),
            "start": int(ctx.parameters.get("start", 1)),
            "sort": ctx.parameters.get("sort", "sim"),
        }
        if "filter" in ctx.parameters and ctx.parameters["filter"]:
            params["filter"] = ctx.parameters["filter"]

        cred_def = credential_registry.require("naver_search")
        request_opts = apply_authentication(
            cred_def.authenticate,
            {
                "method": "GET",
                "url": f"{_BASE_URL}/{endpoint}.json",
                "params": params,
            },
            ctx.credentials,
        )
        response = await ctx.http_client.request(**request_opts)
        response.raise_for_status()
        body = response.json()
        return {
            "http_status": response.status_code,
            "total": body.get("total", 0),
            "items": _format_items(body.get("items", [])),
        }

    return _runner


def _common_parameters(
    *, max_display: int = 100, max_start: int = 1000, default_sort: str = "sim"
) -> list[FieldDef]:
    return [
        FieldDef(
            name="query",
            display_name="Search Query",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="display",
            display_name="Display Count",
            kind=FieldKind.NUMBER,
            default=10,
            type_options={"min": 1, "max": max_display},
        ),
        FieldDef(
            name="start",
            display_name="Start Index",
            kind=FieldKind.NUMBER,
            default=1,
            type_options={"min": 1, "max": max_start},
        ),
        FieldDef(
            name="sort",
            display_name="Sort",
            kind=FieldKind.STRING,
            default=default_sort,
            description="Naver-specific sort key; see endpoint docs.",
        ),
    ]


blog_definition = ToolDefinition(
    key="naver_search_blog",
    display_name="Naver Blog Search",
    description="Search Korean blogs via the Naver Search API.",
    icon_id="naver",
    category="search",
    parameters=_common_parameters(),
    credential_definition_keys=["naver_search"],
    runner=_make_runner("blog"),
)

news_definition = ToolDefinition(
    key="naver_search_news",
    display_name="Naver News Search",
    description="Search Korean news articles via the Naver Search API.",
    icon_id="naver",
    category="search",
    parameters=_common_parameters(),
    credential_definition_keys=["naver_search"],
    runner=_make_runner("news"),
)

image_definition = ToolDefinition(
    key="naver_search_image",
    display_name="Naver Image Search",
    description="Search images via the Naver Search API.",
    icon_id="naver",
    category="search",
    parameters=[
        *_common_parameters(),
        FieldDef(
            name="filter",
            display_name="Image Size Filter",
            kind=FieldKind.SELECT,
            default="all",
            options=[
                {"name": "All", "value": "all"},
                {"name": "Large", "value": "large"},
                {"name": "Medium", "value": "medium"},
                {"name": "Small", "value": "small"},
            ],
        ),
    ],
    credential_definition_keys=["naver_search"],
    runner=_make_runner("image"),
)

shop_definition = ToolDefinition(
    key="naver_search_shop",
    display_name="Naver Shopping Search",
    description="Search shopping items via the Naver Search API.",
    icon_id="naver",
    category="search",
    parameters=_common_parameters(default_sort="sim"),
    credential_definition_keys=["naver_search"],
    runner=_make_runner("shop"),
)

local_definition = ToolDefinition(
    key="naver_search_local",
    display_name="Naver Local Search",
    description="Search local places via the Naver Search API.",
    icon_id="naver",
    category="search",
    parameters=_common_parameters(max_display=5, max_start=1, default_sort="random"),
    credential_definition_keys=["naver_search"],
    runner=_make_runner("local"),
)
