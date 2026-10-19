"""Prepare an ``mcp_oauth2`` credential for the authorization redirect.

Moved out of ``oauth_service`` (F-15). Discovers the authorization server,
picks client registration (manual client_id → Client ID Metadata Document →
DCR; see ``mcp_oauth_spec.choose_client_registration``), stores the canonical
``resource`` URI and returns the issuer the callback must see in ``iss``
(RFC 9207). Whenever metadata is fetched the issuer must be established
(RFC 8414 §3.3) or the start fails with ``McpOAuthIssuerUnverified``.

With dynamic client registration off, AS metadata is fetched only when the
credential names ``authorization_server_url``; a fully manual config without
it has no recorded issuer, so ``iss`` cannot be checked for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.credentials import mcp_oauth_spec as spec
from app.credentials.mcp_oauth_client import (
    AuthorizationServerIssuerError,
    AuthorizationServerMetadata,
    build_pkce_pair,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    dynamic_client_registration,
    select_grant_type_and_authentication,
)


class McpOAuthIssuerUnverified(HTTPException):
    """The authorization server's issuer could not be established."""

    def __init__(self) -> None:
        super().__init__(
            status_code=400, detail="authorization server issuer could not be verified"
        )


async def _verified_metadata(authorization_server_url: str) -> AuthorizationServerMetadata:
    try:
        metadata = await discover_authorization_server_metadata(authorization_server_url)
    except AuthorizationServerIssuerError as exc:
        raise McpOAuthIssuerUnverified() from exc
    if metadata.issuer is None:  # discovery guarantees it; never skip the iss check
        raise McpOAuthIssuerUnverified()
    return metadata


@dataclass(slots=True)
class PreparedOAuth:
    data: dict[str, Any]
    code_verifier: str | None
    redirect_uri: str
    issuer: str | None = None
    iss_required: bool = False


async def _discover(server_url: str) -> tuple[AuthorizationServerMetadata, list[str]]:
    authorization_server_url = server_url
    protected_resource_scopes: list[str] = []
    try:
        protected_metadata = await discover_protected_resource_metadata(server_url)
        authorization_server_url = protected_metadata.authorization_servers[0]
        raw_scopes = protected_metadata.raw.get("scopes_supported")
        if isinstance(raw_scopes, list):
            protected_resource_scopes = [str(v) for v in raw_scopes]
    except Exception:
        # Some providers expose auth-server metadata directly at server_url.
        authorization_server_url = server_url
    return await _verified_metadata(authorization_server_url), protected_resource_scopes


async def _register(
    prepared: dict[str, Any],
    metadata: AuthorizationServerMetadata,
    *,
    redirect_uri: str,
    scope: str | None,
) -> str:
    if prepared.get("client_registration") == "cimd" or spec.CIMD_PATH in str(
        prepared.get("client_id") or ""
    ):
        # A stale CIMD client_id must not masquerade as a pre-registered client.
        prepared.pop("client_id", None)
    registration = spec.choose_client_registration(
        metadata,
        client_id=str(prepared["client_id"]) if prepared.get("client_id") else None,
        registered_by=prepared.get("client_registration"),
    )
    if registration == "cimd":
        prepared.update(
            client_id=spec.client_metadata_document_url(),
            client_registration="cimd",
            grant_type="pkce",
            authentication="none",
        )
        return spec.client_metadata_redirect_uri() or redirect_uri
    if registration == "dcr" and metadata.registration_endpoint and not prepared.get("client_id"):
        registered = await dynamic_client_registration(
            registration_endpoint=metadata.registration_endpoint,
            redirect_uri=redirect_uri,
            client_name="Moldy",
            scope=scope,
            grant_type=prepared["grant_type"],
            authentication=prepared["authentication"],
        )
        prepared["client_id"] = registered.client_id
        if registered.client_secret:
            prepared["client_secret"] = registered.client_secret
    if registration is not None:
        prepared["client_registration"] = registration
    return redirect_uri


async def prepare_mcp_oauth_data(data: dict[str, Any], *, redirect_uri: str) -> PreparedOAuth:
    server_url = data.get("server_url")
    if not server_url:
        return PreparedOAuth(data=data, code_verifier=None, redirect_uri=redirect_uri)

    prepared = dict(data)
    prepared["resource"] = spec.canonical_resource_uri(str(server_url))
    use_dcr = bool(prepared.get("use_dynamic_client_registration", True))
    scope: str | None = str(prepared["scope"]) if prepared.get("scope") else None
    result = PreparedOAuth(data=prepared, code_verifier=None, redirect_uri=redirect_uri)

    if use_dcr:
        metadata, protected_resource_scopes = await _discover(str(server_url))
        if not scope and metadata.scopes_supported:
            scope = " ".join(metadata.scopes_supported)
        if not scope and protected_resource_scopes:
            scope = " ".join(protected_resource_scopes)
        selection = select_grant_type_and_authentication(
            grant_types=metadata.grant_types_supported,
            token_endpoint_auth_methods=metadata.token_endpoint_auth_methods_supported,
            code_challenge_methods=metadata.code_challenge_methods_supported,
        )
        prepared.update(
            {
                "auth_url": metadata.authorization_endpoint,
                "access_token_url": metadata.token_endpoint,
                "registration_url": metadata.registration_endpoint,
                "grant_type": selection.grant_type,
                "authentication": selection.authentication,
            }
        )
        if scope:
            prepared["scope"] = scope
        result.redirect_uri = await _register(
            prepared, metadata, redirect_uri=redirect_uri, scope=scope
        )
        result.issuer = metadata.issuer
        result.iss_required = metadata.iss_parameter_supported
    elif prepared.get("authorization_server_url"):
        metadata = await _verified_metadata(str(prepared["authorization_server_url"]))
        result.issuer = metadata.issuer
        result.iss_required = metadata.iss_parameter_supported

    if str(prepared.get("grant_type") or "pkce") == "pkce":
        pair = build_pkce_pair()
        result.code_verifier = pair.code_verifier
        prepared["code_challenge"] = pair.code_challenge

    auth_url = prepared.get("auth_url") or prepared.get("authorization_url")
    if not prepared.get("client_id") or not auth_url:
        raise HTTPException(
            status_code=400,
            detail="credential must contain client_id and auth_url",
        )
    prepared["auth_url"] = str(auth_url)
    return result


__all__ = ["McpOAuthIssuerUnverified", "PreparedOAuth", "prepare_mcp_oauth_data"]
