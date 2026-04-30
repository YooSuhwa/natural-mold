"""OAuth2 client credential for MCP servers (client_credentials grant)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind


async def _refresh(credentials: dict[str, Any]) -> dict[str, Any]:
    token_url = credentials.get("token_url")
    client_id = credentials.get("client_id")
    client_secret = credentials.get("client_secret")
    if not token_url or not client_id or not client_secret:
        raise RuntimeError(
            "mcp_oauth2: token_url, client_id and client_secret are required"
        )
    data: dict[str, Any] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    scope = credentials.get("scope")
    if scope:
        data["scope"] = scope
    refresh_token = credentials.get("refresh_token")
    if refresh_token:
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        if scope:
            data["scope"] = scope

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=data)
        response.raise_for_status()
        body = response.json()
    expires_in = int(body.get("expires_in") or 3600)
    return {
        "access_token": body.get("access_token", ""),
        "expires_at": time.time() + expires_in,
    }


definition = CredentialDefinition(
    key="mcp_oauth2",
    display_name="MCP OAuth2 Client",
    icon_id="plug",
    category="mcp",
    properties=[
        FieldDef(
            name="token_url",
            display_name="Token URL",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="client_id",
            display_name="Client ID",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="client_secret",
            display_name="Client Secret",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
        FieldDef(
            name="scope",
            display_name="Scope",
            kind=FieldKind.STRING,
            required=False,
        ),
        FieldDef(
            name="access_token",
            display_name="Access Token",
            kind=FieldKind.PASSWORD,
            required=False,
            type_options={"password": True, "expirable": True},
        ),
        FieldDef(
            name="refresh_token",
            display_name="Refresh Token",
            kind=FieldKind.PASSWORD,
            required=False,
            type_options={"password": True},
        ),
        FieldDef(
            name="expires_at",
            display_name="Expires At",
            kind=FieldKind.NUMBER,
            required=False,
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "headers": {
                "Authorization": "=Bearer {{ $credentials.access_token }}",
            }
        }
    ),
    pre_authentication=_refresh,
)
