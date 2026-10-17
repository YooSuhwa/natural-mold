"""MCP OAuth metadata discovery (RFC 9728 protected resource, RFC 8414 server).

Split out of ``mcp_oauth_client`` (F-15). ``new_http_client`` is the single
HTTP client factory for every MCP OAuth module; callers look it up at call
time so tests can swap in a mock transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_TIMEOUT = 15.0


class AuthorizationServerIssuerError(ValueError):
    """Metadata lacks ``issuer`` or names another server (RFC 8414 §3.3)."""


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)


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

    @property
    def issuer(self) -> str | None:
        value = self.raw.get("issuer")
        return value if isinstance(value, str) and value else None

    @property
    def iss_parameter_supported(self) -> bool:
        """RFC 9207 ``authorization_response_iss_parameter_supported``."""
        return self.raw.get("authorization_response_iss_parameter_supported") is True

    @property
    def client_id_metadata_document_supported(self) -> bool:
        return self.raw.get("client_id_metadata_document_supported") is True


def validate_oauth_url(url: str) -> None:
    parsed = httpx.URL((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("OAuth URL must use HTTP or HTTPS protocol")
    if not parsed.host:
        raise ValueError("OAuth URL must include a host")


def _path_without_trailing_slash(url: httpx.URL) -> str:
    path = url.path.rstrip("/")
    return "" if path == "/" else path


def _origin(parsed: httpx.URL) -> str:
    origin = f"{parsed.scheme}://{parsed.host}"
    return f"{origin}:{parsed.port}" if parsed.port is not None else origin


async def get_json(url: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    validate_oauth_url(url)
    close_client = client is None
    active_client = client or new_http_client()
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
    origin = _origin(parsed)
    candidates = ([f"{origin}/.well-known/oauth-protected-resource{path}"] if path else []) + [
        f"{origin}/.well-known/oauth-protected-resource"
    ]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            body = await get_json(candidate, client=client)
            servers = body.get("authorization_servers")
            if isinstance(servers, list) and all(isinstance(v, str) for v in servers) and servers:
                return ProtectedResourceMetadata(authorization_servers=servers, raw=body)
        except Exception as exc:  # noqa: BLE001 - try next metadata URL
            last_error = exc
    raise ValueError(
        f"Failed to discover protected resource metadata from {', '.join(candidates)}: {last_error}"
    )


def _authorization_server_candidates(authorization_server_url: str) -> list[str]:
    parsed = httpx.URL(authorization_server_url)
    origin = _origin(parsed)
    path = _path_without_trailing_slash(parsed)
    if not path:
        return [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
    return [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
        f"{authorization_server_url.rstrip('/')}/.well-known/oauth-authorization-server",
        f"{authorization_server_url.rstrip('/')}/.well-known/openid-configuration",
        f"{origin}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/openid-configuration",
    ]


def _parse_authorization_server(body: dict[str, Any]) -> AuthorizationServerMetadata:
    authorization_endpoint = body.get("authorization_endpoint")
    token_endpoint = body.get("token_endpoint")
    if not isinstance(authorization_endpoint, str) or not isinstance(token_endpoint, str):
        raise ValueError("authorization_endpoint and token_endpoint are required")
    registration = body.get("registration_endpoint")
    return AuthorizationServerMetadata(
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        registration_endpoint=registration if isinstance(registration, str) else None,
        grant_types_supported=[
            str(v) for v in body.get("grant_types_supported", ["authorization_code"])
        ],
        token_endpoint_auth_methods_supported=[
            str(v)
            for v in body.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
        ],
        code_challenge_methods_supported=[
            str(v) for v in body.get("code_challenge_methods_supported", [])
        ],
        scopes_supported=[str(v) for v in body.get("scopes_supported", [])],
        raw=body,
    )


async def discover_authorization_server_metadata(
    authorization_server_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> AuthorizationServerMetadata:
    validate_oauth_url(authorization_server_url)
    candidates = _authorization_server_candidates(authorization_server_url)

    last_error: Exception | None = None
    first_valid: AuthorizationServerMetadata | None = None
    issuer_rejected = False
    for candidate in candidates:
        try:
            metadata = _parse_authorization_server(await get_json(candidate, client=client))
            # RFC 8414 §3.3: the issuer must be identical to the AS URL we asked
            # about, whichever well-known path (incl. origin-root fallbacks) served
            # it — otherwise a hostile document could pick the issuer that the
            # RFC 9207 ``iss`` check later trusts.
            if metadata.issuer != authorization_server_url:
                issuer_rejected = True
                raise AuthorizationServerIssuerError(f"issuer mismatch at {candidate}")
            if metadata.registration_endpoint:
                return metadata
            if first_valid is None:
                first_valid = metadata
        except Exception as exc:  # noqa: BLE001 - try next metadata URL
            last_error = exc
    if first_valid is not None:
        return first_valid
    if issuer_rejected:
        raise AuthorizationServerIssuerError(
            f"No metadata for {authorization_server_url} names it as issuer"
        )
    raise ValueError(
        "Failed to discover OAuth authorization server metadata from "
        f"{', '.join(candidates)}: {last_error}"
    )
