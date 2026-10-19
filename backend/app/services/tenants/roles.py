"""Company role listing and full-set replacement."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.membership_sync import capture_user
from app.models.organization import TenantMember, TenantRoleGrant
from app.schemas.tenant_members import TenantRolePage, TenantRoleView
from app.services import audit_service
from app.services.tenants.member_write import TenantMemberWrite, lock_tenant_member_write
from app.services.tenants.pagination import (
    TenantCursor,
    TenantCursorScope,
    TenantPageQuery,
    decode_cursor,
    encode_cursor,
)
from app.services.tenants.versioning import bump_timestamp
from app.tenancy.admin_context import TenantAdminContext
from app.tenancy.context import ROLE_ADAPTER, TenantRole


@dataclass(frozen=True, slots=True)
class TenantRolePageResult:
    page: TenantRolePage
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantRoleMutationResult:
    view: TenantRoleView
    updated_at: datetime


async def list_roles(
    db: AsyncSession, context: TenantAdminContext, query: TenantPageQuery
) -> TenantRolePageResult:
    if "can_manage_tenant" not in context.permissions:
        raise authorization_error("permission_required")
    tenant_id = context.tenant.id
    scope = TenantCursorScope(list_kind="tenant_roles", tenant_id=tenant_id, filter_key="")
    cursor = decode_cursor(query.cursor, scope)
    statement = select(TenantMember).where(
        TenantMember.tenant_id == tenant_id,
        TenantMember.status != "removed",
        exists().where(
            TenantRoleGrant.tenant_id == tenant_id,
            TenantRoleGrant.user_id == TenantMember.user_id,
        ),
    )
    if cursor is not None:
        statement = statement.where(
            or_(
                TenantMember.created_at > cursor.sort_value,
                and_(
                    TenantMember.created_at == cursor.sort_value,
                    TenantMember.user_id > cursor.position,
                ),
            )
        )
    members = tuple(
        (
            await db.scalars(
                statement.order_by(TenantMember.created_at, TenantMember.user_id).limit(
                    query.limit + 1
                )
            )
        ).all()
    )
    selected = members[: query.limit]
    user_ids = tuple(member.user_id for member in selected)
    grants = (
        tuple(
            (
                await db.scalars(
                    select(TenantRoleGrant)
                    .where(
                        TenantRoleGrant.tenant_id == tenant_id,
                        TenantRoleGrant.user_id.in_(user_ids),
                    )
                    .order_by(TenantRoleGrant.user_id, TenantRoleGrant.role)
                )
            ).all()
        )
        if user_ids
        else ()
    )
    items = tuple(
        TenantRoleView(
            user_id=member.user_id,
            roles=ROLE_ADAPTER.validate_python(
                tuple(grant.role for grant in grants if grant.user_id == member.user_id)
            ),
        )
        for member in selected
    )
    next_cursor = None
    if len(members) > query.limit and selected:
        last = selected[-1]
        next_cursor = encode_cursor(
            TenantCursor(scope=scope, sort_value=last.created_at, position=last.user_id)
        )
    return TenantRolePageResult(
        TenantRolePage(items=items, next_cursor=next_cursor, has_more=next_cursor is not None),
        context.tenant.updated_at,
    )


async def replace_roles(
    command: TenantMemberWrite, user_id: UUID, roles: tuple[TenantRole, ...]
) -> TenantRoleMutationResult:
    context = await lock_tenant_member_write(command, lock_children=True, lock_organizations=True)
    member = context.member(user_id)
    if member is None or member.status != "active":
        raise authorization_error("not_found")
    current_rows = context.user_roles(user_id)
    current = set(ROLE_ADAPTER.validate_python(tuple(row.role for row in current_rows)))
    wanted = set(roles)
    if current == wanted:
        return TenantRoleMutationResult(
            TenantRoleView(user_id=user_id, roles=tuple(sorted(wanted))),
            context.tenant.updated_at,
        )
    active_admins = context.active_admin_ids()
    if "admin" in current - wanted and user_id in active_admins and len(active_admins) == 1:
        raise authorization_error("last_tenant_admin")
    try:
        sync = await capture_user(command.db, command.tenant_id, user_id, None)
        for row in current_rows:
            if row.role not in wanted:
                await command.db.delete(row)
        for role in sorted(wanted - current):
            command.db.add(
                TenantRoleGrant(
                    tenant_id=command.tenant_id,
                    user_id=user_id,
                    role=role,
                    granted_by=command.actor.id,
                )
            )
        await sync.queue(command.db)  # tenant role tuples (company-scoped: no marker)
        context.tenant.updated_at = bump_timestamp(context.tenant.updated_at)
        await _record_role_audits(command, user_id, current, wanted)
        await command.db.commit()
    except SQLAlchemyError:
        await command.db.rollback()
        raise
    return TenantRoleMutationResult(
        TenantRoleView(user_id=user_id, roles=tuple(sorted(wanted))),
        context.tenant.updated_at,
    )


async def _record_role_audits(
    command: TenantMemberWrite,
    user_id: UUID,
    current: set[TenantRole],
    wanted: set[TenantRole],
) -> None:
    for action, changed in (
        (audit_actions.TENANT_ROLE_REVOKE, sorted(current - wanted)),
        (audit_actions.TENANT_ROLE_GRANT, sorted(wanted - current)),
    ):
        if not changed:
            continue
        await audit_service.record_event(
            command.db,
            actor_type="user",
            actor_user_id=command.actor.id,
            actor_email_snapshot=command.actor.email,
            owner_user_id=command.actor.id,
            action=action,
            target_type="tenant_role",
            target_id=user_id,
            outcome="success",
            request=command.request,
            metadata={"tenant_id": str(command.tenant_id), "roles": changed},
        )
