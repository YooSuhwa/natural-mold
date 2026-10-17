"""MCP OAuth 2.1 client helpers.

This module implements the client-side pieces Moldy needs to connect to
remote MCP servers such as Atlassian Rovo: PKCE, grant selection, Dynamic
Client Registration and the authorization URL. Metadata discovery lives in
``mcp_oauth_discovery`` and token requests in ``mcp_oauth_tokens`` (F-15
split); both are re-exported here so existing imports keep working.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from app.credentials import mcp_oauth_discovery as discovery
from app.credentials.mcp_oauth_discovery import (
    DEFAULT_TIMEOUT,
    AuthorizationServerIssuerError,
    AuthorizationServerMetadata,
    ProtectedResourceMetadata,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    validate_oauth_url,
)
from app.credentials.mcp_oauth_tokens import (
    AuthenticationMethod,
    exchange_authorization_code,
    fetch_client_credentials_token,
    refresh_access_token,
)

GrantType = Literal["pkce", "authorization_code", "client_credentials"]


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


@dataclass(frozen=True)
class GrantSelection:
    grant_type: GrantType
    authentication: AuthenticationMethod


@dataclass(frozen=True)
class RegistrationResult:
    client_id: str
    client_secret: str | None
    raw: dict[str, Any]


def build_pkce_pair() -> PkcePair:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def select_grant_type_and_authentication(
    *,
    grant_types: list[str],
    token_endpoint_auth_methods: list[str],
    code_challenge_methods: list[str],
) -> GrantSelection:
    grants = set(grant_types)
    auth_methods = set(token_endpoint_auth_methods)
    challenge_methods = set(code_challenge_methods)
    if "authorization_code" in grants and "S256" in challenge_methods and "none" in auth_methods:
        return GrantSelection(grant_type="pkce", authentication="none")
    if (
        "authorization_code" in grants
        and "S256" in challenge_methods
        and "client_secret_basic" in auth_methods
    ):
        return GrantSelection(grant_type="pkce", authentication="header")
    if (
        "authorization_code" in grants
        and "S256" in challenge_methods
        and "client_secret_post" in auth_methods
    ):
        return GrantSelection(grant_type="pkce", authentication="body")
    if "authorization_code" in grants and "client_secret_basic" in auth_methods:
        return GrantSelection(grant_type="authorization_code", authentication="header")
    if "authorization_code" in grants and "client_secret_post" in auth_methods:
        return GrantSelection(grant_type="authorization_code", authentication="body")
    if "client_credentials" in grants and "client_secret_basic" in auth_methods:
        return GrantSelection(grant_type="client_credentials", authentication="header")
    if "client_credentials" in grants and "client_secret_post" in auth_methods:
        return GrantSelection(grant_type="client_credentials", authentication="body")
    raise ValueError("OAuth server does not advertise a supported grant/authentication method")


def _registration_auth_method(authentication: AuthenticationMethod) -> str:
    if authentication == "none":
        return "none"
    if authentication == "body":
        return "client_secret_post"
    return "client_secret_basic"


async def dynamic_client_registration(
    *,
    registration_endpoint: str,
    redirect_uri: str,
    client_name: str,
    scope: str | None,
    grant_type: GrantType,
    authentication: AuthenticationMethod,
    client: httpx.AsyncClient | None = None,
) -> RegistrationResult:
    validate_oauth_url(registration_endpoint)
    grant_types = (
        ["client_credentials"]
        if grant_type == "client_credentials"
        else ["authorization_code", "refresh_token"]
    )
    payload: dict[str, Any] = {
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": _registration_auth_method(authentication),
        "grant_types": grant_types,
        "response_types": ["code"],
        "client_name": client_name,
    }
    if scope:
        payload["scope"] = scope

    close_client = client is None
    active_client = client or discovery.new_http_client()
    try:
        response = await active_client.post(registration_endpoint, json=payload)
        response.raise_for_status()
        body = response.json()
    finally:
        if close_client:
            await active_client.aclose()
    if not isinstance(body, dict) or not isinstance(body.get("client_id"), str):
        raise ValueError("Dynamic Client Registration response must include client_id")
    client_secret = body.get("client_secret")
    return RegistrationResult(
        client_id=body["client_id"],
        client_secret=client_secret if isinstance(client_secret, str) else None,
        raw=body,
    )


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str | None,
    code_challenge: str | None = None,
    resource: str | None = None,
) -> str:
    validate_oauth_url(authorization_endpoint)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scope:
        params["scope"] = scope
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    if resource:
        # RFC 8707: the MCP server's canonical URI (plan 11.4.1).
        params["resource"] = resource
    return f"{authorization_endpoint}?{urlencode(params)}"


__all__ = [
    "DEFAULT_TIMEOUT",
    "AuthenticationMethod",
    "AuthorizationServerIssuerError",
    "AuthorizationServerMetadata",
    "GrantSelection",
    "GrantType",
    "PkcePair",
    "ProtectedResourceMetadata",
    "RegistrationResult",
    "build_authorization_url",
    "build_pkce_pair",
    "discover_authorization_server_metadata",
    "discover_protected_resource_metadata",
    "dynamic_client_registration",
    "exchange_authorization_code",
    "fetch_client_credentials_token",
    "refresh_access_token",
    "select_grant_type_and_authentication",
    "validate_oauth_url",
]
