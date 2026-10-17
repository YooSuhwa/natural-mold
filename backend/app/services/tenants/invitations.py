"""Company invitation lifecycle with hash-only deterministic tokens."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.auth.jwt import create_tenant_invitation_token
from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.models.tenant_invitation import TenantInvitation
from app.schemas.tenant_invitations import (
    TenantInvitationCreate,
    TenantInvitationCreated,
)
from app.services import audit_service
from app.services.tenants.invitation_queries import (
    invitation_query,
    invitation_view,
    pending_same_content,
)
from app.services.tenants.overview import TenantOverviewMutation
from app.services.tenants.versioning import (
    bump_timestamp,
    parse_version,
    require_current_version,
    utc_timestamp,
)
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)
from app.tenancy.limit_capacity import require_invite_capacity


def creation_view(row: TenantInvitation) -> TenantInvitationCreated:
    token = create_tenant_invitation_token(row.id)
    if not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row.token_hash):
        token = None
    return TenantInvitationCreated.model_validate(
        {**invitation_view(row).model_dump(), "token": token}
    )


async def create_invitation(
    mutation: TenantOverviewMutation,
    context: TenantAdminContext,
    payload: TenantInvitationCreate,
) -> tuple[TenantInvitationCreated, bool]:
    locked = await _locked_admin(mutation, context)
    _require_version_syntax(mutation.expected_version)
    role = payload.roles[0] if payload.roles else None
    now, email = datetime.now(UTC), str(payload.email).casefold()
    candidates = await pending_same_content(mutation.db, locked.tenant.id, email, role, now)
    if candidates:
        return await _reissue(mutation, locked, candidates[0]), False
    require_current_version(locked.tenant.updated_at, mutation.expected_version)
    await require_invite_capacity(mutation.db, locked.tenant.id, email)  # H-06 member_limit
    row = _new_invitation(locked, email, role, now)
    mutation.db.add(row)
    await _record(mutation, locked, row, audit_actions.TENANT_INVITATION_CREATE)
    await _finish(mutation, locked)
    return creation_view(row), True


def _require_version_syntax(supplied: str | None) -> None:
    if parse_version(supplied) is None:
        raise authorization_error("stale_version")


async def revoke_invitation(
    mutation: TenantOverviewMutation, context: TenantAdminContext, invitation_id: UUID
) -> None:
    locked = await _locked_context(mutation, context)
    row = await _locked_row(mutation, locked, invitation_id)
    if row.status == "accepted":
        raise authorization_error("tenant_invitation_used")
    if row.status == "revoked":
        return
    _revoke(row, locked.actor.id, datetime.now(UTC))
    await _record(mutation, locked, row, audit_actions.TENANT_INVITATION_REVOKE)
    await _finish(mutation, locked)


async def resend_invitation(
    mutation: TenantOverviewMutation, context: TenantAdminContext, invitation_id: UUID
) -> tuple[TenantInvitationCreated, bool]:
    locked = await _locked_context(mutation, context)
    original = await _locked_row(mutation, locked, invitation_id)
    if original.status == "accepted":
        raise authorization_error("tenant_invitation_used")
    now = datetime.now(UTC)
    pending = await pending_same_content(
        mutation.db, locked.tenant.id, original.email, original.role, now
    )
    if original.status == "revoked" and original.revoked_at is not None:
        for candidate in pending:
            fresh = utc_timestamp(candidate.created_at) >= utc_timestamp(original.revoked_at)
            if fresh and creation_view(candidate).token is not None:
                return await _reissue(mutation, locked, candidate), False
    for row in pending:
        _revoke(row, locked.actor.id, now)
    if original.status != "revoked":
        _revoke(original, locked.actor.id, now)
    replacement = _new_invitation(locked, original.email, original.role, now)
    mutation.db.add(replacement)
    await _record(mutation, locked, replacement, audit_actions.TENANT_INVITATION_RESEND)
    await _finish(mutation, locked)
    return creation_view(replacement), True


async def _locked_context(
    mutation: TenantOverviewMutation, context: TenantAdminContext
) -> TenantAdminContext:
    locked = await _locked_admin(mutation, context)
    require_current_version(locked.tenant.updated_at, mutation.expected_version)
    return locked


async def _locked_admin(
    mutation: TenantOverviewMutation, context: TenantAdminContext
) -> TenantAdminContext:
    return await load_tenant_admin_context(
        mutation.db,
        context.actor,
        TenantAdminTarget(context.tenant.id, "can_manage_tenant"),
    )


async def _locked_row(
    mutation: TenantOverviewMutation, context: TenantAdminContext, invitation_id: UUID
) -> TenantInvitation:
    row = await mutation.db.scalar(
        invitation_query(context.tenant.id)
        .where(TenantInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise authorization_error("not_found")
    return row


def _new_invitation(
    context: TenantAdminContext, email: str, role: str | None, now: datetime
) -> TenantInvitation:
    invitation_id = uuid4()
    token = create_tenant_invitation_token(invitation_id)
    return TenantInvitation(
        id=invitation_id,
        tenant_id=context.tenant.id,
        email=email,
        role=role,
        status="pending",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=context.actor.id,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )


def _revoke(row: TenantInvitation, actor_id: UUID, now: datetime) -> None:
    row.status = "revoked"
    row.revoked_at, row.revoked_by = now, actor_id


async def _record(
    mutation: TenantOverviewMutation,
    context: TenantAdminContext,
    row: TenantInvitation,
    action: str,
) -> None:
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        owner_user_id=context.actor.id,
        action=action,
        target_type="tenant_invitation",
        target_id=row.id,
        target_name_snapshot=row.email,
        outcome="success",
        request=mutation.request,
        metadata={"tenant_id": str(context.tenant.id), "role": row.role},
    )


async def _reissue(
    mutation: TenantOverviewMutation, context: TenantAdminContext, row: TenantInvitation
) -> TenantInvitationCreated:
    """An idempotent retry hands out a live token again, so it is audited (no version bump)."""
    view = creation_view(row)
    if view.token is not None:
        await _record(mutation, context, row, audit_actions.TENANT_INVITATION_REISSUE)
        await mutation.db.commit()
    return view


async def _finish(mutation: TenantOverviewMutation, context: TenantAdminContext) -> None:
    context.tenant.updated_at = bump_timestamp(context.tenant.updated_at)
    await mutation.db.commit()
