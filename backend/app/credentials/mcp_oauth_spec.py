"""MCP authorization spec (2026-07-28) conformance helpers — plan 11.4.1, F-15.

- RFC 8707 ``resource``: the MCP server's canonical URI (lowercase scheme and
  host, default port dropped, no query or fragment, no trailing slash).
- RFC 9207 ``iss``: the authorization response issuer is compared to the
  issuer recorded with the state/PKCE row by plain string equality.
- Client registration order: manually entered client_id → Client ID Metadata
  Document → Dynamic Client Registration (kept for compatibility). The plan
  puts CIMD first; a manual client_id winning is a deliberate deviation.
- Token destination: a token only travels to its own server's canonical origin
  and never in a query string.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from app.config import settings
from app.credentials.mcp_oauth_discovery import AuthorizationServerMetadata

ClientRegistration = Literal["cimd", "pre_registered", "dcr"]
CIMD_PATH = "/.well-known/oauth-client"
CALLBACK_PATH = "/api/oauth2-credential/callback"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_TOKEN_QUERY_KEYS = {"access_token", "token", "id_token", "refresh_token"}


def canonical_resource_uri(url: str) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("MCP server URL must be an absolute HTTP(S) URL")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    # RFC 8707 §2: no fragment; query stripped too (review L4).
    return urlunsplit((scheme, host, parts.path.rstrip("/"), "", ""))


def issuer_matches(*, expected: str | None, received: str | None, required: bool) -> bool:
    """RFC 9207 check. ``required`` = the server advertised the ``iss`` parameter."""

    if expected is None:
        return True
    if received is None:
        return not required
    return received == expected


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


def token_destination_allowed(*, resource: str, request_url: str) -> bool:
    """A token may only be sent to its MCP server's canonical origin, never in a query."""

    if _origin(canonical_resource_uri(resource)) != _origin(request_url):
        return False
    query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(request_url).query)}
    return not (query_keys & _TOKEN_QUERY_KEYS)


def _public_base_url() -> str | None:
    base = (settings.mcp_oauth_client_metadata_base_url or "").strip().rstrip("/")
    return base or None


def client_metadata_document_url() -> str | None:
    base = _public_base_url()
    if base is None:
        return None
    return f"{base}{CIMD_PATH}/{settings.mcp_oauth_client_deployment}"


def client_metadata_redirect_uri() -> str | None:
    base = _public_base_url()
    return f"{base}{CALLBACK_PATH}" if base else None


def client_metadata_document(deployment: str) -> dict[str, Any] | None:
    """The public Client ID Metadata Document, or None when not served."""

    url = client_metadata_document_url()
    redirect_uri = client_metadata_redirect_uri()
    if url is None or redirect_uri is None or deployment != settings.mcp_oauth_client_deployment:
        return None
    return {
        "client_id": url,
        "client_name": "Moldy",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def choose_client_registration(
    metadata: AuthorizationServerMetadata,
    *,
    client_id: str | None,
    registered_by: str | None = None,
) -> ClientRegistration | None:
    """Pick how Moldy identifies itself to the authorization server.

    Deviation from the 11.4.1 order (coordinator decision): a client_id the
    user entered manually wins over CIMD. A client_id Moldy obtained itself
    (``registered_by`` = ``dcr``) does not, and is reused only without CIMD.
    """

    if client_id and registered_by not in ("cimd", "dcr"):
        return "pre_registered"
    if (
        client_metadata_document_url() is not None
        and metadata.client_id_metadata_document_supported
        and "S256" in metadata.code_challenge_methods_supported
    ):
        return "cimd"
    if client_id or metadata.registration_endpoint:
        return "dcr"
    return None


__all__ = [
    "ClientRegistration",
    "canonical_resource_uri",
    "choose_client_registration",
    "client_metadata_document",
    "client_metadata_document_url",
    "client_metadata_redirect_uri",
    "issuer_matches",
    "token_destination_allowed",
]
