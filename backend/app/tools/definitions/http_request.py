"""Generic HTTP Request tool — universal escape hatch.

Accepts any URL/method, optional headers/params/JSON body, and an optional
credential of type ``http_bearer`` / ``http_api_key`` / ``http_basic``. The
credential's ``GenericAuth`` recipe is applied via
:func:`app.credentials.authenticate.apply_authentication` so auth handling stays
consistent with the rest of the system.
"""

from __future__ import annotations

import json as _json
from typing import Any

from app.credentials.authenticate import apply_authentication
from app.credentials.registry import registry as credential_registry
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind
from app.tools.risk import ToolRiskLevel

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]


async def _runner(ctx: ToolRunContext) -> dict[str, Any]:
    params = ctx.parameters
    method = str(params.get("method", "GET")).upper()
    url = params.get("url")
    if not url:
        raise ValueError("'url' parameter is required")

    headers_raw = params.get("headers") or {}
    query_raw = params.get("query_params") or {}
    body_raw = params.get("json_body")
    timeout = float(params.get("timeout") or 30.0)

    # Allow string-encoded JSON for headers/query/body so the UI can keep a
    # single text input even when the user pastes raw JSON.
    headers = _coerce_dict(headers_raw, "headers")
    query = _coerce_dict(query_raw, "query_params")
    json_body = _coerce_dict(body_raw, "json_body") if body_raw else None

    request_opts: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": headers,
        "params": query,
        "timeout": timeout,
    }
    if json_body is not None:
        request_opts["json"] = json_body

    if ctx.credentials is not None:
        # Look up the credential's definition only to find its GenericAuth.
        # The runner does not know the definition_key directly — derive it
        # from the parameter convention: tool authors store their selected
        # credential definition key in ``parameters['_credential_definition_key']``
        # if needed. For the generic HTTP tool we accept whatever auth recipe
        # the picked credential carries.
        ck = params.get("_credential_definition_key")
        if ck:
            cred_def = credential_registry.get(ck)
            if cred_def is not None and cred_def.authenticate is not None:
                request_opts = apply_authentication(
                    cred_def.authenticate, request_opts, ctx.credentials
                )

    response = await ctx.http_client.request(**request_opts)
    body_text = response.text or ""
    body_parsed: Any = body_text
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body_parsed = response.json()
        except ValueError:
            body_parsed = body_text

    return {
        "http_status": response.status_code,
        "headers": dict(response.headers),
        "body": body_parsed,
    }


def _coerce_dict(value: Any, field: str) -> dict[str, Any]:
    if value in (None, "", b""):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = _json.loads(value)
        except ValueError as exc:
            raise ValueError(f"{field} is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} must be a JSON object")
        return parsed
    raise ValueError(f"{field} must be a dict or JSON string")


definition = ToolDefinition(
    key="http_request",
    display_name="HTTP 요청",
    description=(
        "Issue an HTTP request to any URL. Optional credentials (Bearer / API "
        "Key / Basic) are applied to the outgoing request automatically."
    ),
    icon_id="globe",
    category="http",
    parameters=[
        FieldDef(
            name="method",
            display_name="HTTP Method",
            kind=FieldKind.SELECT,
            default="GET",
            required=True,
            options=[{"name": m, "value": m} for m in _METHODS],
        ),
        FieldDef(
            name="url",
            display_name="URL",
            kind=FieldKind.STRING,
            required=True,
            placeholder="https://api.example.com/v1/resource",
        ),
        FieldDef(
            name="headers",
            display_name="Headers (JSON object)",
            kind=FieldKind.JSON,
            default={},
        ),
        FieldDef(
            name="query_params",
            display_name="Query Parameters (JSON object)",
            kind=FieldKind.JSON,
            default={},
        ),
        FieldDef(
            name="json_body",
            display_name="JSON Body",
            kind=FieldKind.JSON,
            default=None,
            display_options={"show": {"method": ["POST", "PUT", "PATCH"]}},
        ),
        FieldDef(
            name="timeout",
            display_name="Timeout (seconds)",
            kind=FieldKind.NUMBER,
            default=30,
            type_options={"min": 1, "max": 300},
        ),
    ],
    credential_definition_keys=["http_bearer", "http_api_key", "http_basic"],
    risk_level=ToolRiskLevel.EXTERNAL_MUTATION,
    requires_approval=True,
    allowed_decisions=("approve", "edit", "reject"),
    trigger_safe=False,
    risk_reason="Can call arbitrary external HTTP endpoints with credentials",
    runner=_runner,
)
