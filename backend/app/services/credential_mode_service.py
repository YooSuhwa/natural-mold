"""Switching a tool's or MCP server's credential mode (plan D15, 10.4, S28; F-04).

The route needs ``can_edit``. ``shared`` -> ``per_user`` keeps existing shares (each
user then has to connect) and records the required credential type, which must be a
registered definition the resource accepts (``credential_mode_invalid``). Moving out of
``per_user`` while a credential is attached lends the owner's account again, so it also
needs the resource's ``can_share`` and the credential's ``can_share`` (S28; someone
else's credential: ``409 credential_reshare_blocked``), and is refused
(``personal_account_shared``) when a personal account would be lent to the people the
resource, or an agent using it, is shared with (10.4). ``none`` needs no credential and
``shared`` needs one (``credential_mode_invalid``). Runs resolve ``per_user`` resources
through the run account's connection (``app.credentials.run_resolution``).
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.personal_accounts import require_unshared_for_personal
from app.authz.resource_gate import CREDENTIAL, MCP_SERVER, TOOL, gate_row
from app.authz.service import AuthzService
from app.credentials.registry import registry as credential_registry
from app.credentials.validation import require_user_credential
from app.exceptions import AppError
from app.models.credential import Credential
from app.models.tool import Tool
from app.schemas.credential_binding import BindingResourceType, CredentialModeIn, CredentialModeOut
from app.services import audit_service
from app.services.credential_binding_service import (
    Target,
    accepted_definition_keys,
    count_for,
)


def kind_of(row: Target) -> BindingResourceType:
    return "tool" if isinstance(row, Tool) else "mcp_server"


async def _require_reshare(
    db: AsyncSession, ctx: OrgContext, authz: AuthzService, row: Target
) -> None:
    async def same() -> Target:
        return row

    spec = TOOL if isinstance(row, Tool) else MCP_SERVER
    await gate_row(authz, db, ctx, spec, row.id, "can_share", same)
    if row.credential_id is None:
        return
    cid = row.credential_id

    async def owned() -> Credential | None:
        return await require_user_credential(db, credential_id=cid, context=ctx)

    try:
        await gate_row(authz, db, ctx, CREDENTIAL, cid, "can_share", owned)
    except (AppError, HTTPException) as error:
        status = error.status if isinstance(error, AppError) else error.status_code
        if status != 404:
            raise
        raise authorization_error("credential_reshare_blocked") from None


async def _definition_key(db: AsyncSession, row: Target, requested: str | None) -> str:
    key = requested
    if key is None and row.credential_id is not None:
        credential = await db.get(Credential, row.credential_id)
        key = None if credential is None else credential.definition_key
    accepted = accepted_definition_keys(row)
    if key is None or credential_registry.get(key) is None or key not in accepted:
        raise authorization_error(
            "credential_mode_invalid",
            {"field": "credential_definition_key", "accepted": sorted(accepted)},
        )
    return key


def _require_consistent(row: Target, mode: str) -> None:
    if (mode == "none") != (row.credential_id is None) and mode != "per_user":
        raise authorization_error("credential_mode_invalid", {"field": "credential_id"})


async def set_mode(
    db: AsyncSession,
    ctx: OrgContext,
    authz: AuthzService,
    row: Target,
    payload: CredentialModeIn,
    *,
    request: Request | None = None,
) -> CredentialModeOut:
    previous = row.credential_mode
    _require_consistent(row, payload.credential_mode)
    if previous == "per_user" != payload.credential_mode and row.credential_id is not None:
        await _require_reshare(db, ctx, authz, row)
    if payload.credential_mode == "per_user":
        row.credential_definition_key = await _definition_key(
            db, row, payload.credential_definition_key
        )
    row.credential_mode = payload.credential_mode
    await db.flush()
    if payload.credential_mode != "per_user":
        await require_unshared_for_personal(db, ctx, row)  # 10.4: lends the owner's account
    kind = kind_of(row)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=ctx.user_id,
        action=audit_actions.CREDENTIAL_MODE_UPDATE,
        target_type=kind,
        target_id=row.id,
        outcome="success",
        request=request,
        metadata={"org_id": str(ctx.org_id), "before": previous, "after": row.credential_mode},
        tenant_id=ctx.tenant_id,
    )
    return CredentialModeOut(
        resource_type=kind,
        resource_id=row.id,
        credential_mode=row.credential_mode,
        credential_definition_key=row.credential_definition_key,
        binding_count=await count_for(db, kind, row.id),
    )


__all__ = ["kind_of", "set_mode"]
