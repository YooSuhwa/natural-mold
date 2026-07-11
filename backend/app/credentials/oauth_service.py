"""OAuth2 flow orchestration for credentials (BE-S7).

Layering contract with ``mcp_oauth_client``: the client owns low-level HTTP
primitives (metadata discovery, dynamic client registration, PKCE, token
exchange); this service owns DB state (``CredentialOAuthState``), credential
payload persistence, and flow orchestration. Routers translate HTTP <->
service calls only.

Transaction policy: the service ``flush``es, the calling router ``commit``s.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.mcp_oauth_client import (
    build_authorization_url,
    build_pkce_pair,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    dynamic_client_registration,
    select_grant_type_and_authentication,
)
from app.credentials.registry import registry
from app.dependencies import CurrentUser
from app.error_codes import credential_forbidden, credential_not_found
from app.models.credential import Credential
from app.models.credential_oauth_state import CredentialOAuthState

OAUTH_STATE_TTL_SECONDS = 30 * 60


@dataclass(slots=True)
class OAuthStartResult:
    authorization_url: str
    state: str


@dataclass(slots=True)
class OAuthCallbackResult:
    credential_id: uuid.UUID
    return_to: str | None


def hash_oauth_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def prepare_mcp_oauth_data(
    data: dict[str, Any],
    *,
    redirect_uri: str,
) -> tuple[dict[str, Any], str | None]:
    server_url = data.get("server_url")
    if not server_url:
        return data, None

    prepared = dict(data)
    use_dcr = bool(prepared.get("use_dynamic_client_registration", True))
    scope: str | None = str(prepared["scope"]) if prepared.get("scope") else None
    protected_resource_scopes: list[str] = []

    if use_dcr:
        authorization_server_url = str(server_url)
        try:
            protected_metadata = await discover_protected_resource_metadata(str(server_url))
            authorization_server_url = protected_metadata.authorization_servers[0]
            raw_scopes = protected_metadata.raw.get("scopes_supported")
            if isinstance(raw_scopes, list):
                protected_resource_scopes = [str(v) for v in raw_scopes]
        except Exception:
            # Some providers expose auth-server metadata directly at server_url.
            authorization_server_url = str(server_url)

        auth_metadata = await discover_authorization_server_metadata(authorization_server_url)
        if not scope and auth_metadata.scopes_supported:
            scope = " ".join(auth_metadata.scopes_supported)
        if not scope and protected_resource_scopes:
            scope = " ".join(protected_resource_scopes)

        selection = select_grant_type_and_authentication(
            grant_types=auth_metadata.grant_types_supported,
            token_endpoint_auth_methods=auth_metadata.token_endpoint_auth_methods_supported,
            code_challenge_methods=auth_metadata.code_challenge_methods_supported,
        )
        prepared.update(
            {
                "auth_url": auth_metadata.authorization_endpoint,
                "access_token_url": auth_metadata.token_endpoint,
                "registration_url": auth_metadata.registration_endpoint,
                "grant_type": selection.grant_type,
                "authentication": selection.authentication,
            }
        )
        if scope:
            prepared["scope"] = scope

        if auth_metadata.registration_endpoint and not prepared.get("client_id"):
            registered = await dynamic_client_registration(
                registration_endpoint=auth_metadata.registration_endpoint,
                redirect_uri=redirect_uri,
                client_name="Moldy",
                scope=scope,
                grant_type=selection.grant_type,
                authentication=selection.authentication,
            )
            prepared["client_id"] = registered.client_id
            if registered.client_secret:
                prepared["client_secret"] = registered.client_secret

    grant_type = str(prepared.get("grant_type") or "pkce")
    code_challenge: str | None = None
    code_verifier: str | None = None
    if grant_type == "pkce":
        pair = build_pkce_pair()
        code_verifier = pair.code_verifier
        code_challenge = pair.code_challenge
        prepared["code_challenge"] = code_challenge

    client_id = prepared.get("client_id")
    auth_url = prepared.get("auth_url") or prepared.get("authorization_url")
    if not client_id or not auth_url:
        raise HTTPException(
            status_code=400,
            detail="credential must contain client_id and auth_url",
        )

    prepared["auth_url"] = str(auth_url)
    return prepared, code_verifier if code_challenge else None


async def persist_credential_payload(
    cred: Credential,
    data: dict[str, Any],
) -> None:
    blob, key_id, field_keys = credential_service.encrypt_data(data)
    cred.data_encrypted = blob
    cred.key_id = key_id
    cred.field_keys = field_keys


async def gc_oauth_states(db: AsyncSession, *, now: datetime) -> None:
    """Delete expired or consumed OAuth states. Reusable from scheduler GC."""

    await db.execute(
        delete(CredentialOAuthState).where(
            or_(
                CredentialOAuthState.expires_at <= now,
                CredentialOAuthState.consumed_at.is_not(None),
            )
        )
    )


async def start_oauth(
    db: AsyncSession,
    *,
    user: CurrentUser,
    credential_id: uuid.UUID,
    fallback_redirect_uri: str,
    return_to: str | None,
) -> OAuthStartResult:
    """Build an authorization URL and persist the in-flight state token.

    The credential's ``data`` must already contain ``client_id`` and either
    ``authorization_url`` or be a definition that infers one (the API consumer
    is responsible for filling these in before starting the flow).
    """

    cred = await credential_service.get_for_user(db, credential_id, user.id)
    if cred is None:
        raise credential_not_found()
    definition = registry.get(cred.definition_key)
    if definition is None or definition.pre_authentication is None:
        raise HTTPException(
            status_code=400,
            detail=f"definition '{cred.definition_key}' does not support OAuth2",
        )
    data = await credential_service.decrypt_with_external(cred.data_encrypted)
    redirect_uri = data.get("redirect_uri") or fallback_redirect_uri
    code_verifier: str | None = None
    if cred.definition_key == "mcp_oauth2":
        data, code_verifier = await prepare_mcp_oauth_data(
            data,
            redirect_uri=redirect_uri,
        )
    else:
        auth_url = data.get("authorization_url") or data.get("auth_url")
        if str(data.get("grant_type") or "") == "pkce":
            pair = build_pkce_pair()
            code_verifier = pair.code_verifier
            data["code_challenge"] = pair.code_challenge
        if auth_url:
            data["auth_url"] = str(auth_url)

    client_id = data.get("client_id")
    auth_url_base = data.get("auth_url") or data.get("authorization_url")
    if not client_id or not auth_url_base:
        raise HTTPException(
            status_code=400,
            detail="credential must contain client_id and auth_url",
        )

    now = utcnow_naive()
    await gc_oauth_states(db, now=now)

    state = secrets.token_urlsafe(32)
    db.add(
        CredentialOAuthState(
            state_hash=hash_oauth_state(state),
            credential_id=credential_id,
            user_id=user.id,
            redirect_uri=str(redirect_uri),
            code_verifier=code_verifier,
            origin="credential",
            return_to=return_to,
            metadata_json={"definition_key": cred.definition_key},
            expires_at=now + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
        )
    )
    await persist_credential_payload(cred, data)
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user.id,
        action="update",
        source="api",
        metadata={"data_changed": True, "trigger": "oauth_start"},
    )
    await db.flush()

    authorization_url = build_authorization_url(
        authorization_endpoint=str(auth_url_base),
        client_id=str(client_id),
        redirect_uri=str(redirect_uri),
        state=state,
        scope=str(data["scope"]) if data.get("scope") else None,
        code_challenge=(
            data.get("code_challenge") if isinstance(data.get("code_challenge"), str) else None
        ),
    )
    return OAuthStartResult(authorization_url=authorization_url, state=state)


async def handle_callback(
    db: AsyncSession,
    *,
    code: str,
    state: str,
) -> OAuthCallbackResult:
    """Exchange ``code`` for tokens via the definition's ``pre_authentication``."""

    now = utcnow_naive()
    pending = (
        await db.execute(
            select(CredentialOAuthState)
            .where(
                CredentialOAuthState.state_hash == hash_oauth_state(state),
                CredentialOAuthState.consumed_at.is_(None),
                CredentialOAuthState.expires_at > now,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pending is None:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    credential_id = pending.credential_id

    result = await db.execute(select(Credential).where(Credential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise credential_not_found()
    # Verify the credential belongs to the user who started the OAuth flow.
    # Prevents a scenario where an attacker with a known state token could
    # complete another user's OAuth flow and update their credential.
    if cred.user_id != pending.user_id:
        raise credential_forbidden()
    definition = registry.get(cred.definition_key)
    if definition is None or definition.pre_authentication is None:
        raise HTTPException(
            status_code=400,
            detail=f"definition '{cred.definition_key}' does not support OAuth2",
        )

    data = await credential_service.decrypt_with_external(cred.data_encrypted)
    # Use the authorization-code grant: hand the code off to pre_authentication
    # by stashing it in the data payload; definition implementations switch on
    # presence of ``authorization_code``.
    data["authorization_code"] = code
    data["redirect_uri"] = pending.redirect_uri
    if pending.code_verifier:
        data["code_verifier"] = pending.code_verifier
    previous_refresh_token = data.get("refresh_token")
    refreshed = await definition.pre_authentication(data)
    data.pop("authorization_code", None)
    data.pop("code_verifier", None)
    data.pop("code_challenge", None)
    data.update(refreshed)
    if previous_refresh_token and not data.get("refresh_token"):
        data["refresh_token"] = previous_refresh_token
    data["oauth_connected_at"] = datetime.now(UTC).isoformat()

    await persist_credential_payload(cred, data)
    cred.status = "active"
    pending.consumed_at = now
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=cred.user_id,
        action="refresh",
        source="api",
        metadata={"trigger": "oauth_callback"},
    )
    await db.flush()
    return OAuthCallbackResult(credential_id=credential_id, return_to=pending.return_to)
