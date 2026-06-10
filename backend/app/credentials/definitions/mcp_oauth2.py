"""OAuth2 credential for remote MCP servers."""

from __future__ import annotations

from typing import Any

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind
from app.credentials.mcp_oauth_client import (
    AuthenticationMethod,
    exchange_authorization_code,
    fetch_client_credentials_token,
    refresh_access_token,
)


def _token_endpoint(credentials: dict[str, Any]) -> str:
    endpoint = credentials.get("access_token_url") or credentials.get("token_url")
    if not endpoint:
        raise RuntimeError("mcp_oauth2: access_token_url is required")
    return str(endpoint)


def _client_id(credentials: dict[str, Any]) -> str:
    client_id = credentials.get("client_id")
    if not client_id:
        raise RuntimeError("mcp_oauth2: client_id is required")
    return str(client_id)


def _legacy_client_secret_post(credentials: dict[str, Any]) -> bool:
    return (
        credentials.get("authentication") is None
        and credentials.get("token_url") is not None
        and credentials.get("client_secret") is not None
        and credentials.get("server_url") is None
    )


def _authentication(credentials: dict[str, Any]) -> AuthenticationMethod:
    if _legacy_client_secret_post(credentials):
        return "body"
    raw = str(credentials.get("authentication") or "none")
    if raw in {"none", "header", "body"}:
        return raw  # type: ignore[return-value]
    raise RuntimeError(f"mcp_oauth2: unsupported authentication method '{raw}'")


async def _refresh(credentials: dict[str, Any]) -> dict[str, Any]:
    token_endpoint = _token_endpoint(credentials)
    client_id = _client_id(credentials)
    client_secret = credentials.get("client_secret")
    authentication = _authentication(credentials)
    scope = credentials.get("scope")

    authorization_code = credentials.get("authorization_code")
    if authorization_code:
        redirect_uri = credentials.get("redirect_uri")
        if not redirect_uri:
            raise RuntimeError("mcp_oauth2: redirect_uri is required for authorization code")
        return await exchange_authorization_code(
            token_endpoint=token_endpoint,
            code=str(authorization_code),
            redirect_uri=str(redirect_uri),
            client_id=client_id,
            client_secret=str(client_secret) if client_secret else None,
            authentication=authentication,
            code_verifier=(
                str(credentials["code_verifier"]) if credentials.get("code_verifier") else None
            ),
            previous_refresh_token=(
                str(credentials["refresh_token"]) if credentials.get("refresh_token") else None
            ),
        )

    refresh_token = credentials.get("refresh_token")
    if refresh_token:
        return await refresh_access_token(
            token_endpoint=token_endpoint,
            refresh_token=str(refresh_token),
            client_id=client_id,
            client_secret=str(client_secret) if client_secret else None,
            authentication=authentication,
            scope=str(scope) if scope else None,
        )

    if str(credentials.get("grant_type") or "") == "client_credentials" or (
        _legacy_client_secret_post(credentials) and not authorization_code and not refresh_token
    ):
        return await fetch_client_credentials_token(
            token_endpoint=token_endpoint,
            client_id=client_id,
            client_secret=str(client_secret) if client_secret else None,
            authentication=authentication,
            scope=str(scope) if scope else None,
        )

    raise RuntimeError("mcp_oauth2: refresh_token or authorization_code is required")


definition = CredentialDefinition(
    key="mcp_oauth2",
    display_name="MCP OAuth2 Client",
    icon_id="plug",
    category="mcp",
    documentation_url=(
        "https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization"
    ),
    properties=[
        FieldDef(
            name="server_url",
            display_name="MCP Server URL",
            kind=FieldKind.STRING,
            required=True,
            placeholder="https://mcp.atlassian.com/v1/mcp/authv2",
        ),
        FieldDef(
            name="use_dynamic_client_registration",
            display_name="Use Dynamic Client Registration",
            kind=FieldKind.TOGGLE,
            default=True,
        ),
        FieldDef(
            name="auth_url",
            display_name="Authorization URL",
            kind=FieldKind.STRING,
            required=False,
        ),
        FieldDef(
            name="access_token_url",
            display_name="Access Token URL",
            kind=FieldKind.STRING,
            required=False,
        ),
        FieldDef(
            name="registration_url",
            display_name="Registration URL",
            kind=FieldKind.STRING,
            required=False,
        ),
        FieldDef(
            name="client_id",
            display_name="Client ID",
            kind=FieldKind.STRING,
            required=False,
        ),
        FieldDef(
            name="client_secret",
            display_name="Client Secret",
            kind=FieldKind.PASSWORD,
            required=False,
            type_options={"password": True},
        ),
        FieldDef(
            name="scope",
            display_name="Scope",
            kind=FieldKind.STRING,
            required=False,
        ),
        FieldDef(
            name="grant_type",
            display_name="Grant Type",
            kind=FieldKind.SELECT,
            default="pkce",
            options=[
                {"name": "PKCE", "value": "pkce"},
                {"name": "Authorization Code", "value": "authorization_code"},
                {"name": "Client Credentials", "value": "client_credentials"},
            ],
        ),
        FieldDef(
            name="authentication",
            display_name="Token Endpoint Authentication",
            kind=FieldKind.SELECT,
            default="none",
            options=[
                {"name": "None", "value": "none"},
                {"name": "Header", "value": "header"},
                {"name": "Body", "value": "body"},
            ],
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
