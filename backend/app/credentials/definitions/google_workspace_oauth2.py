"""Google Workspace OAuth2 (Gmail, Calendar, Drive — shared client)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — OAuth endpoint URL, not a secret


async def _refresh(credentials: dict[str, Any]) -> dict[str, Any]:
    """Exchange the stored refresh_token for a fresh access_token."""

    refresh_token = credentials.get("refresh_token")
    client_id = credentials.get("client_id")
    client_secret = credentials.get("client_secret")
    if not refresh_token or not client_id or not client_secret:
        raise RuntimeError(
            "google_workspace_oauth2: client_id, client_secret and refresh_token "
            "are all required for refresh"
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
        response.raise_for_status()
        body = response.json()
    expires_in = int(body.get("expires_in") or 3600)
    return {
        "access_token": body.get("access_token", ""),
        "expires_at": time.time() + expires_in,
    }


definition = CredentialDefinition(
    key="google_workspace_oauth2",
    display_name="Google Workspace OAuth2",
    icon_id="google",
    documentation_url="https://developers.google.com/identity/protocols/oauth2",
    category="oauth",
    properties=[
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
            name="refresh_token",
            display_name="Refresh Token",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True, "expirable": False},
            description=("Long-lived refresh token issued via the OAuth2 consent flow."),
        ),
        FieldDef(
            name="access_token",
            display_name="Access Token",
            kind=FieldKind.PASSWORD,
            required=False,
            type_options={"password": True, "expirable": True},
        ),
        FieldDef(
            name="expires_at",
            display_name="Expires At",
            kind=FieldKind.NUMBER,
            required=False,
            description="Unix epoch seconds for the current access_token expiry.",
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
