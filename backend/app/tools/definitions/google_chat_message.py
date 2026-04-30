"""Google Chat — post a message via an incoming webhook.

The webhook URL is sensitive but not strictly an OAuth credential — accept it
either as an inline parameter (for quick tests) or as an ``http_bearer`` /
``http_api_key`` credential whose ``token`` field is the URL itself. When a
credential is bound, its value overrides the inline ``webhook_url`` parameter.
"""

from __future__ import annotations

from typing import Any

from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind


async def _runner(ctx: ToolRunContext) -> dict[str, Any]:
    webhook_url: str | None = None
    if ctx.credentials is not None:
        # Accept either {"token": URL} (http_bearer style) or {"webhook_url": URL}
        webhook_url = (
            ctx.credentials.get("webhook_url") or ctx.credentials.get("token")
        )
    if not webhook_url:
        webhook_url = ctx.parameters.get("webhook_url")
    if not webhook_url:
        raise ValueError(
            "webhook_url is required (either as a parameter or via a credential)"
        )

    text = ctx.parameters.get("message") or ""
    if not text:
        raise ValueError("'message' parameter is required")

    response = await ctx.http_client.post(webhook_url, json={"text": text})
    response.raise_for_status()
    return {
        "http_status": response.status_code,
        "delivered": True,
    }


definition = ToolDefinition(
    key="google_chat_message",
    display_name="Google Chat — Send Message",
    description=(
        "Post a message to a Google Chat space via incoming webhook. The "
        "webhook URL may be supplied inline or stored as an HTTP credential."
    ),
    icon_id="chat",
    category="messaging",
    parameters=[
        FieldDef(
            name="message",
            display_name="Message",
            kind=FieldKind.MULTILINE,
            required=True,
            type_options={"rows": 4},
        ),
        FieldDef(
            name="webhook_url",
            display_name="Webhook URL (optional if credential provided)",
            kind=FieldKind.PASSWORD,
            type_options={"password": True},
            description=(
                "If a credential is attached, its value overrides this field."
            ),
        ),
    ],
    credential_definition_keys=["http_bearer"],
    runner=_runner,
)
