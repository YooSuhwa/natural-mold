"""MCP OAuth token-endpoint requests (split out of ``mcp_oauth_client``, F-15).

Every token request (code exchange, refresh, client credentials) carries the
RFC 8707 ``resource`` parameter when the caller knows the MCP server's
canonical URI (plan 11.4.1).
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx

from app.credentials import mcp_oauth_discovery as discovery

AuthenticationMethod = Literal["none", "header", "body"]


def _token_auth(
    *,
    client_id: str,
    client_secret: str | None,
    authentication: AuthenticationMethod,
) -> tuple[dict[str, Any], httpx.BasicAuth | None]:
    data: dict[str, Any] = {"client_id": client_id}
    if authentication == "header":
        return data, httpx.BasicAuth(client_id, client_secret or "")
    if authentication == "body" and client_secret:
        data["client_secret"] = client_secret
    return data, None


def _with_optional(data: dict[str, Any], **values: str | None) -> dict[str, Any]:
    for key, value in values.items():
        if value:
            data[key] = value
    return data


async def exchange_authorization_code(
    *,
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
    authentication: AuthenticationMethod,
    code_verifier: str | None,
    previous_refresh_token: str | None = None,
    resource: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    base, auth = _token_auth(
        client_id=client_id, client_secret=client_secret, authentication=authentication
    )
    data = {**base, "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    _with_optional(data, code_verifier=code_verifier, resource=resource)
    return await _post_token(
        token_endpoint=token_endpoint,
        data=data,
        auth=auth,
        previous_refresh_token=previous_refresh_token,
        client=client,
    )


async def refresh_access_token(
    *,
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None,
    authentication: AuthenticationMethod,
    scope: str | None = None,
    resource: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    base, auth = _token_auth(
        client_id=client_id, client_secret=client_secret, authentication=authentication
    )
    data = {**base, "grant_type": "refresh_token", "refresh_token": refresh_token}
    _with_optional(data, scope=scope, resource=resource)
    return await _post_token(
        token_endpoint=token_endpoint,
        data=data,
        auth=auth,
        previous_refresh_token=refresh_token,
        client=client,
    )


async def fetch_client_credentials_token(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    authentication: AuthenticationMethod,
    scope: str | None = None,
    resource: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    base, auth = _token_auth(
        client_id=client_id, client_secret=client_secret, authentication=authentication
    )
    data = _with_optional({**base, "grant_type": "client_credentials"}, scope=scope)
    _with_optional(data, resource=resource)
    return await _post_token(
        token_endpoint=token_endpoint,
        data=data,
        auth=auth,
        previous_refresh_token=None,
        client=client,
    )


async def _post_token(
    *,
    token_endpoint: str,
    data: dict[str, Any],
    auth: httpx.BasicAuth | None,
    previous_refresh_token: str | None,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    discovery.validate_oauth_url(token_endpoint)
    close_client = client is None
    active_client = client or discovery.new_http_client()
    try:
        if auth is None:
            response = await active_client.post(token_endpoint, data=data)
        else:
            response = await active_client.post(token_endpoint, data=data, auth=auth)
        response.raise_for_status()
        body = response.json()
    finally:
        if close_client:
            await active_client.aclose()
    if not isinstance(body, dict) or not body.get("access_token"):
        raise ValueError("OAuth token response must include access_token")

    expires_in = int(body.get("expires_in") or 3600)
    refresh_token = body.get("refresh_token") or previous_refresh_token
    patch: dict[str, Any] = {
        "access_token": body["access_token"],
        "expires_at": time.time() + expires_in,
        "token_type": body.get("token_type") or "Bearer",
    }
    if refresh_token:
        patch["refresh_token"] = refresh_token
    for key in ("id_token", "scope", "expires_in"):
        if key in body:
            patch[key] = body[key]
    return patch
