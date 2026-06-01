"""Google Custom Search — web/image/news variants.

Each variant is its own :class:`ToolDefinition`. Authentication relies on a
``google_search`` credential carrying ``api_key`` + ``cse_id`` (applied as
query string parameters, so the credential definition's GenericAuth handles it).
"""

from __future__ import annotations

from typing import Any

from app.credentials.authenticate import apply_authentication
from app.credentials.registry import registry as credential_registry
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind

_URL = "https://www.googleapis.com/customsearch/v1"


def _make_runner(variant: str):
    async def _runner(ctx: ToolRunContext) -> dict[str, Any]:
        if ctx.credentials is None:
            raise ValueError("google_search credential is required")

        params: dict[str, Any] = {
            "q": ctx.parameters["query"],
            "num": int(ctx.parameters.get("num", 5)),
        }
        if variant == "image":
            params["searchType"] = "image"
        elif variant == "news":
            params["tbm"] = "nws"

        cred_def = credential_registry.require("google_search")
        request_opts = apply_authentication(
            cred_def.authenticate,
            {"method": "GET", "url": _URL, "params": params},
            ctx.credentials,
        )
        response = await ctx.http_client.request(**request_opts)
        response.raise_for_status()
        body = response.json()
        return {
            "http_status": response.status_code,
            "total": body.get("searchInformation", {}).get("totalResults"),
            "items": body.get("items", []),
        }

    return _runner


def _params() -> list[FieldDef]:
    return [
        FieldDef(
            name="query",
            display_name="Search Query",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="num",
            display_name="Result Count",
            kind=FieldKind.NUMBER,
            default=5,
            type_options={"min": 1, "max": 10},
        ),
    ]


web_definition = ToolDefinition(
    key="google_search_web",
    display_name="Google 웹 검색",
    description="Search the web via Google Custom Search.",
    icon_id="google",
    category="search",
    parameters=_params(),
    credential_definition_keys=["google_search"],
    runner=_make_runner("web"),
)

image_definition = ToolDefinition(
    key="google_search_image",
    display_name="Google 이미지 검색",
    description="Search images via Google Custom Search.",
    icon_id="google",
    category="search",
    parameters=_params(),
    credential_definition_keys=["google_search"],
    runner=_make_runner("image"),
)

news_definition = ToolDefinition(
    key="google_search_news",
    display_name="Google 뉴스 검색",
    description="Search news via Google Custom Search.",
    icon_id="google",
    category="search",
    parameters=_params(),
    credential_definition_keys=["google_search"],
    runner=_make_runner("news"),
)
