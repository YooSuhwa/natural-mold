"""Locked company invitation lookups and stable list queries."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.registration_queries import DEFAULT_COMPANY_ID
from app.authz.tenant_org_write_locks import TenantOrgWriteTarget, lock_tenant_org_writes
from app.models.organization import Tenant
from app.models.tenant_invitation import TenantInvitation
from app.schemas.tenant_invitations import TenantInvitationPage, TenantInvitationView
from app.services.tenants.pagination import (
    TenantCursor,
    TenantCursorScope,
    TenantPageQuery,
    decode_cursor,
    encode_cursor,
)
from app.services.tenants.versioning import utc_timestamp
from app.tenancy.admin_context import TenantAdminContext


def invitation_view(row: TenantInvitation) -> TenantInvitationView:
    status = row.status
    if status == "pending" and utc_timestamp(row.expires_at) <= datetime.now(UTC):
        status = "expired"
    return TenantInvitationView.model_validate(
        {
            "id": row.id,
            "email": row.email,
            "role": row.role,
            "expires_at": utc_timestamp(row.expires_at),
            "created_at": utc_timestamp(row.created_at),
            "status": status,
        }
    )


async def list_invitations(
    db: AsyncSession, context: TenantAdminContext, query: TenantPageQuery
) -> TenantInvitationPage:
    if "can_manage_tenant" not in context.permissions:
        raise authorization_error("permission_required")
    scope = TenantCursorScope(
        list_kind="tenant_invitations", tenant_id=context.tenant.id, filter_key=""
    )
    cursor = decode_cursor(query.cursor, scope)
    statement = invitation_query(context.tenant.id)
    if cursor is not None:
        statement = statement.where(
            or_(
                TenantInvitation.created_at < cursor.sort_value,
                (
                    (TenantInvitation.created_at == cursor.sort_value)
                    & (TenantInvitation.id < cursor.position)
                ),
            )
        )
    rows = list(
        (
            await db.scalars(
                statement.order_by(
                    TenantInvitation.created_at.desc(), TenantInvitation.id.desc()
                ).limit(query.limit + 1)
            )
        ).all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    next_cursor = None
    if more:
        last = selected[-1]
        next_cursor = encode_cursor(
            TenantCursor(scope=scope, sort_value=utc_timestamp(last.created_at), position=last.id)
        )
    return TenantInvitationPage(
        items=[invitation_view(row) for row in selected],
        next_cursor=next_cursor,
        has_more=more,
    )


def invitation_query(tenant_id: UUID) -> Select[tuple[TenantInvitation]]:
    return select(TenantInvitation).where(TenantInvitation.tenant_id == tenant_id)


async def locked_invitation_by_digest(
    db: AsyncSession, digest: str
) -> tuple[Tenant, TenantInvitation]:
    with db.no_autoflush:
        tenant_id = await db.scalar(
            select(TenantInvitation.tenant_id).where(TenantInvitation.token_hash == digest)
        )
        if tenant_id is None:
            raise authorization_error("tenant_invitation_invalid")
        locked = await lock_tenant_org_writes(
            db,
            (TenantOrgWriteTarget(tenant_id, (), "tenant_invitation_invalid"),),
        )
        invitation = await db.scalar(
            select(TenantInvitation)
            .where(TenantInvitation.token_hash == digest, TenantInvitation.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invitation is None:
            raise authorization_error("tenant_invitation_invalid")
        return locked.tenants[0], invitation


async def pending_same_content(
    db: AsyncSession,
    tenant_id: UUID,
    email: str,
    role: str | None,
    now: datetime,
) -> list[TenantInvitation]:
    role_filter = TenantInvitation.role.is_(None) if role is None else TenantInvitation.role == role
    return list(
        (
            await db.scalars(
                invitation_query(tenant_id)
                .where(
                    TenantInvitation.email == email,
                    role_filter,
                    TenantInvitation.status == "pending",
                    TenantInvitation.expires_at > now,
                )
                .order_by(TenantInvitation.created_at.desc(), TenantInvitation.id.desc())
                .with_for_update()
            )
        ).all()
    )


async def locked_registration_invitation(
    db: AsyncSession, digest: str, include_bootstrap: bool
) -> tuple[Tenant, TenantInvitation, Tenant | None]:
    with db.no_autoflush:
        tenant_id = await db.scalar(
            select(TenantInvitation.tenant_id).where(TenantInvitation.token_hash == digest)
        )
        if tenant_id is None:
            raise authorization_error("tenant_invitation_invalid")
        targets = [TenantOrgWriteTarget(tenant_id, (), "tenant_invitation_invalid")]
        if include_bootstrap:
            targets.append(
                TenantOrgWriteTarget(DEFAULT_COMPANY_ID, (), "organization_bootstrap_required")
            )
        locked = await lock_tenant_org_writes(db, targets)
        tenants = {tenant.id: tenant for tenant in locked.tenants}
        invitation = await db.scalar(
            select(TenantInvitation)
            .where(TenantInvitation.token_hash == digest, TenantInvitation.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invitation is None:
            raise authorization_error("tenant_invitation_invalid")
        return (
            tenants[tenant_id],
            invitation,
            tenants.get(DEFAULT_COMPANY_ID) if include_bootstrap else None,
        )
