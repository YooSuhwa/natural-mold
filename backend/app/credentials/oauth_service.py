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

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.credentials import service as credential_service
from app.credentials import tenant_encryption
from app.credentials.boundary import open_values
from app.credentials.mcp_oauth_client import build_authorization_url, build_pkce_pair
from app.credentials.mcp_oauth_prepare import prepare_mcp_oauth_data
from app.credentials.mcp_oauth_spec import issuer_matches
from app.credentials.mcp_oauth_step_up import merge_scopes
from app.credentials.oauth_callback_scope import live_callback_state
from app.credentials.registry import registry
from app.error_codes import credential_forbidden, credential_not_found
from app.models.credential import Credential
from app.models.credential_oauth_state import CredentialOAuthState

OAUTH_STATE_TTL_SECONDS = 30 * 60


class OAuthResponseRejected(HTTPException):
    """Rejected authorization response; the state row is already consumed."""

    def __init__(self) -> None:
        super().__init__(status_code=400, detail="invalid authorization response")


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


async def persist_credential_payload(
    cred: Credential,
    data: dict[str, Any],
) -> None:
    await tenant_encryption.seal(cred, data)


async def gc_oauth_states(db: AsyncSession, *, now: datetime) -> None:
    """Delete expired or consumed OAuth states.

    Currently invoked opportunistically from ``start_oauth`` only; public so
    a scheduler GC job can adopt it without reaching into router internals.
    """

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
    context: OrgContext,
    credential_id: uuid.UUID,
    fallback_redirect_uri: str,
    return_to: str | None,
    requested_scope: str | None = None,
) -> OAuthStartResult:
    """Build an authorization URL and persist the in-flight state token.

    ``requested_scope`` (scope step-up, 11.4.1) widens only this authorization
    request: the merged scope rides on the state row and replaces the stored
    scope only when the callback succeeds.

    The credential's ``data`` must already contain ``client_id`` and either
    ``authorization_url`` or be a definition that infers one (the API consumer
    is responsible for filling these in before starting the flow).
    """

    cred = await credential_service.get_for_user(db, credential_id, context)
    if cred is None:
        raise credential_not_found()
    definition = registry.get(cred.definition_key)
    if definition is None or definition.pre_authentication is None:
        raise HTTPException(
            status_code=400,
            detail=f"definition '{cred.definition_key}' does not support OAuth2",
        )
    data = await open_values(cred, "refresh")
    redirect_uri = data.get("redirect_uri") or fallback_redirect_uri
    code_verifier: str | None = None
    # RFC 9207: the issuer travels with the state/PKCE row (plan 11.4.1).
    state_metadata: dict[str, Any] = {"definition_key": cred.definition_key}
    if cred.definition_key == "mcp_oauth2":
        prepared = await prepare_mcp_oauth_data(data, redirect_uri=redirect_uri)
        data, code_verifier, redirect_uri = (
            prepared.data,
            prepared.code_verifier,
            prepared.redirect_uri,
        )
        if prepared.issuer:
            state_metadata.update(issuer=prepared.issuer, iss_required=prepared.iss_required)
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

    auth_scope = str(data["scope"]) if data.get("scope") else None
    if requested_scope:
        auth_scope = merge_scopes(auth_scope, requested_scope)
        state_metadata["requested_scope"] = auth_scope

    now = utcnow_naive()
    await gc_oauth_states(db, now=now)

    state = secrets.token_urlsafe(32)
    db.add(
        CredentialOAuthState(
            state_hash=hash_oauth_state(state),
            credential_id=credential_id,
            user_id=context.user_id,
            redirect_uri=str(redirect_uri),
            code_verifier=code_verifier,
            origin="credential",
            return_to=return_to,
            metadata_json=state_metadata,
            expires_at=now + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
        )
    )
    await persist_credential_payload(cred, data)
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=context.user_id,
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
        scope=auth_scope,
        code_challenge=(
            data.get("code_challenge") if isinstance(data.get("code_challenge"), str) else None
        ),
        resource=str(data["resource"]) if data.get("resource") else None,
    )
    return OAuthStartResult(authorization_url=authorization_url, state=state)


async def handle_callback(
    db: AsyncSession,
    *,
    code: str,
    state: str,
    iss: str | None = None,
) -> OAuthCallbackResult:
    """Exchange ``code`` for tokens via the definition's ``pre_authentication``.

    The authorization response ``iss`` is checked against the issuer stored
    with the state row first; a mismatch never reaches the token endpoint and
    the error carries no detail (RFC 9207, plan 11.4.1).
    """

    now = utcnow_naive()
    pending = await live_callback_state(db, hash_oauth_state(state), now)
    if pending is None:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    state_metadata = pending.metadata_json or {}
    expected_issuer = state_metadata.get("issuer")
    if not issuer_matches(
        expected=expected_issuer if isinstance(expected_issuer, str) else None,
        received=iss,
        required=state_metadata.get("iss_required") is True,
    ):
        # One-shot: the state can't be replayed with another iss (caller commits).
        pending.consumed_at = now
        await db.flush()
        raise OAuthResponseRejected()
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
    if cred.org_id is None or cred.tenant_id is None:
        raise credential_not_found()
    await resolve_private_actor(
        db,
        PrivateResourceActor(user_id=pending.user_id, org_id=cred.org_id, tenant_id=cred.tenant_id),
    )
    definition = registry.get(cred.definition_key)
    if definition is None or definition.pre_authentication is None:
        raise HTTPException(
            status_code=400,
            detail=f"definition '{cred.definition_key}' does not support OAuth2",
        )

    data = await open_values(cred, "refresh")
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
    requested_scope = state_metadata.get("requested_scope")
    if isinstance(requested_scope, str) and "scope" not in refreshed:
        data["scope"] = requested_scope  # step-up applies only on success
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
