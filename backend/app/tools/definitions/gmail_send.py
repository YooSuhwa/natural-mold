"""Gmail Send — uses the Workspace OAuth2 credential's access_token directly.

Calls the Gmail v1 REST endpoint instead of pulling in ``google-api-python-client``,
keeping the runtime dependency surface small. The access_token refresh is the
credential layer's job (``google_workspace_oauth2.pre_authentication``); this
tool just signs the request with whatever token is currently stored.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

from app.credentials.authenticate import apply_authentication
from app.credentials.registry import registry as credential_registry
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind
from app.tools.risk import ToolRiskLevel

_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


async def _runner(ctx: ToolRunContext) -> dict[str, Any]:
    if ctx.credentials is None:
        raise ValueError("google_workspace_oauth2 credential is required")

    to = ctx.parameters["to"]
    subject = ctx.parameters["subject"]
    body = ctx.parameters["body"]

    message = MIMEText(body, "plain", "utf-8")
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    cred_def = credential_registry.require("google_workspace_oauth2")
    request_opts = apply_authentication(
        cred_def.authenticate,
        {
            "method": "POST",
            "url": _GMAIL_SEND_URL,
            "json": {"raw": raw},
        },
        ctx.credentials,
    )
    response = await ctx.http_client.request(**request_opts)
    response.raise_for_status()
    body_json = response.json()
    return {
        "http_status": response.status_code,
        "message_id": body_json.get("id"),
        "thread_id": body_json.get("threadId"),
    }


definition = ToolDefinition(
    key="gmail_send",
    display_name="Gmail — Send Email",
    description="Send an email via Gmail using a Google Workspace OAuth2 credential.",
    icon_id="gmail",
    category="email",
    parameters=[
        FieldDef(
            name="to",
            display_name="To",
            kind=FieldKind.STRING,
            required=True,
            placeholder="recipient@example.com",
        ),
        FieldDef(
            name="subject",
            display_name="Subject",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="body",
            display_name="Body",
            kind=FieldKind.MULTILINE,
            required=True,
            type_options={"rows": 8},
        ),
    ],
    credential_definition_keys=["google_workspace_oauth2"],
    risk_level=ToolRiskLevel.EXTERNAL_MUTATION,
    requires_approval=True,
    allowed_decisions=("approve", "edit", "reject"),
    trigger_safe=False,
    risk_reason="Sends email to external recipients",
    runner=_runner,
)
