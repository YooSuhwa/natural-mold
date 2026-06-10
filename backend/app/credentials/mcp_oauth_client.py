"""MCP OAuth 2.1 client helpers.

This module implements the client-side pieces Moldy needs to connect to
remote MCP servers such as Atlassian Rovo: protected resource discovery,
authorization-server metadata discovery, Dynamic Client Registration, PKCE,
authorization-code exchange, and refresh-token exchange.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

GrantType = Literal["pkce", "authorization_code", "client_credentials"]
AuthenticationMethod = Literal["none", "header", "body"]

DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    authorization_servers: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    grant_types_supported: list[str]
    token_endpoint_auth_methods_supported: list[str]
    code_challenge_methods_supported: list[str]
    scopes_supported: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class GrantSelection:
    grant_type: GrantType
    authentication: AuthenticationMethod


@dataclass(frozen=True)
class RegistrationResult:
    client_id: str
    client_secret: str | None
    raw: dict[str, Any]


def validate_oauth_url(url: str) -> None:
    parsed = httpx.URL((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("OAuth URL must use HTTP or HTTPS protocol")
    if not parsed.host:
        raise ValueError("OAuth URL must include a host")


def build_pkce_pair() -> PkcePair:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def _path_without_trailing_slash(url: httpx.URL) -> str:
    path = url.path.rstrip("/")
    return "" if path == "/" else path


async def _get_json(url: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    validate_oauth_url(url)
    close_client = client is None
    active_client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        response = await active_client.get(url)
        response.raise_for_status()
        body = response.json()
    finally:
        if close_client:
            await active_client.aclose()
    if not isinstance(body, dict):
        raise ValueError(f"OAuth metadata response from {url} must be a JSON object")
    return body


async def discover_protected_resource_metadata(
    resource_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProtectedResourceMetadata:
    validate_oauth_url(resource_url)
    parsed = httpx.URL(resource_url)
    path = _path_without_trailing_slash(parsed)
    urls = [f"{parsed.scheme}://{parsed.host}"]
    if parsed.port is not None:
        urls = [f"{parsed.scheme}://{parsed.host}:{parsed.port}"]
    origin = urls[0]
    candidates = ([f"{origin}/.well-known/oauth-protected-resource{path}"] if path else []) + [
        f"{origin}/.well-known/oauth-protected-resource"
    ]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            body = await _get_json(candidate, client=client)
            servers = body.get("authorization_servers")
            if isinstance(servers, list) and all(isinstance(v, str) for v in servers) and servers:
                return ProtectedResourceMetadata(authorization_servers=servers, raw=body)
        except Exception as exc:  # noqa: BLE001 - try next metadata URL
            last_error = exc
    raise ValueError(
        f"Failed to discover protected resource metadata from {', '.join(candidates)}: {last_error}"
    )


async def discover_authorization_server_metadata(
    authorization_server_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> AuthorizationServerMetadata:
    validate_oauth_url(authorization_server_url)
    parsed = httpx.URL(authorization_server_url)
    origin = f"{parsed.scheme}://{parsed.host}"
    if parsed.port is not None:
        origin = f"{origin}:{parsed.port}"
    path = _path_without_trailing_slash(parsed)
    candidates = (
        [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{authorization_server_url.rstrip('/')}/.well-known/oauth-authorization-server",
            f"{authorization_server_url.rstrip('/')}/.well-known/openid-configuration",
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
        if path
        else [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
    )

    last_error: Exception | None = None
    first_valid: AuthorizationServerMetadata | None = None
    for candidate in candidates:
        try:
            body = await _get_json(candidate, client=client)
            authorization_endpoint = body.get("authorization_endpoint")
            token_endpoint = body.get("token_endpoint")
            if not isinstance(authorization_endpoint, str) or not isinstance(token_endpoint, str):
                raise ValueError("authorization_endpoint and token_endpoint are required")
            metadata = AuthorizationServerMetadata(
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                registration_endpoint=(
                    body.get("registration_endpoint")
                    if isinstance(body.get("registration_endpoint"), str)
                    else None
                ),
                grant_types_supported=[
                    str(v) for v in body.get("grant_types_supported", ["authorization_code"])
                ],
                token_endpoint_auth_methods_supported=[
                    str(v)
                    for v in body.get(
                        "token_endpoint_auth_methods_supported", ["client_secret_basic"]
                    )
                ],
                code_challenge_methods_supported=[
                    str(v) for v in body.get("code_challenge_methods_supported", [])
                ],
                scopes_supported=[str(v) for v in body.get("scopes_supported", [])],
                raw=body,
            )
            if metadata.registration_endpoint:
                return metadata
            if first_valid is None:
                first_valid = metadata
        except Exception as exc:  # noqa: BLE001 - try next metadata URL
            last_error = exc
    if first_valid is not None:
        return first_valid
    raise ValueError(
        "Failed to discover OAuth authorization server metadata from "
        f"{', '.join(candidates)}: {last_error}"
    )


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
    active_client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
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
    return f"{authorization_endpoint}?{urlencode(params)}"


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
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    base, auth = _token_auth(
        client_id=client_id,
        client_secret=client_secret,
        authentication=authentication,
    )
    data = {
        **base,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
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
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    base, auth = _token_auth(
        client_id=client_id,
        client_secret=client_secret,
        authentication=authentication,
    )
    data = {
        **base,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if scope:
        data["scope"] = scope
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
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    base, auth = _token_auth(
        client_id=client_id,
        client_secret=client_secret,
        authentication=authentication,
    )
    data = {**base, "grant_type": "client_credentials"}
    if scope:
        data["scope"] = scope
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
    validate_oauth_url(token_endpoint)
    close_client = client is None
    active_client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
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


__all__ = [
    "AuthenticationMethod",
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
