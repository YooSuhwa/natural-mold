"""Company member list and versioned membership mutations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.models.organization import TenantMember, TenantRoleGrant
from app.models.user import User
from app.schemas.tenant_members import (
    TenantMemberAdminView,
    TenantMemberPage,
    TenantMemberProjection,
)
from app.services import audit_service
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from app.services.tenants.member_write import (
    TenantMemberWrite,
    TenantMemberWriteContext,
    lock_tenant_member_write,
)
from app.services.tenants.pagination import (
    TenantCursor,
    TenantCursorScope,
    TenantPageQuery,
    decode_cursor,
    encode_cursor,
)
from app.services.tenants.versioning import bump_timestamp
from app.tenancy.admin_context import TenantAdminContext
from app.tenancy.context import ROLE_ADAPTER
from app.tenancy.limit_capacity import require_member_capacity


@dataclass(frozen=True, slots=True)
class TenantMemberPageResult:
    page: TenantMemberPage
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantMemberMutationResult:
    view: TenantMemberAdminView
    updated_at: datetime


async def list_members(
    db: AsyncSession, context: TenantAdminContext, query: TenantPageQuery
) -> TenantMemberPageResult:
    if "can_manage_tenant" not in context.permissions:
        raise authorization_error("permission_required")
    scope = TenantCursorScope(
        list_kind="tenant_members", tenant_id=context.tenant.id, filter_key=""
    )
    cursor = decode_cursor(query.cursor, scope)
    statement = (
        select(TenantMember, User)
        .join(User, User.id == TenantMember.user_id)
        .where(
            TenantMember.tenant_id == context.tenant.id,
            TenantMember.status != "removed",
        )
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
    rows = (
        await db.execute(
            statement.order_by(TenantMember.created_at, TenantMember.user_id).limit(query.limit + 1)
        )
    ).all()
    selected = rows[: query.limit]
    user_ids = tuple(member.user_id for member, _user in selected)
    role_rows = (
        tuple(
            (
                await db.scalars(
                    select(TenantRoleGrant)
                    .where(
                        TenantRoleGrant.tenant_id == context.tenant.id,
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
        TenantMemberAdminView.from_projection(
            TenantMemberProjection(
                member,
                user,
                ROLE_ADAPTER.validate_python(
                    tuple(role.role for role in role_rows if role.user_id == member.user_id)
                ),
            )
        )
        for member, user in selected
    )
    next_cursor = None
    if len(rows) > query.limit and selected:
        last = selected[-1][0]
        next_cursor = encode_cursor(
            TenantCursor(scope=scope, sort_value=last.created_at, position=last.user_id)
        )
    return TenantMemberPageResult(
        TenantMemberPage(items=items, next_cursor=next_cursor, has_more=next_cursor is not None),
        context.tenant.updated_at,
    )


async def add_member(command: TenantMemberWrite, user_id: UUID) -> TenantMemberMutationResult:
    context = await lock_tenant_member_write(command, lock_children=True)
    existing = context.member(user_id)
    if existing is not None:
        if existing.status != "active":
            raise authorization_error("not_found")
        return TenantMemberMutationResult(
            await _member_view(command.db, context, existing), context.tenant.updated_at
        )
    user = await command.db.scalar(
        select(User).where(User.id == user_id, User.is_active.is_(True)).with_for_update()
    )
    if user is None:
        raise authorization_error("not_found")
    await require_member_capacity(command.db, command.tenant_id)  # H-06 member_limit
    member = TenantMember(tenant_id=command.tenant_id, user_id=user_id, status="active")
    command.db.add(member)
    await _commit_member_change(command, context, member, audit_actions.TENANT_MEMBER_ADD)
    await command.db.refresh(member)
    return TenantMemberMutationResult(
        TenantMemberAdminView.from_projection(TenantMemberProjection(member, user, ())),
        context.tenant.updated_at,
    )


async def set_member_status(
    command: TenantMemberWrite,
    user_id: UUID,
    status: Literal["active", "suspended"],
) -> TenantMemberMutationResult:
    context = await lock_tenant_member_write(command, lock_children=True)
    member = context.member(user_id)
    if member is None or member.status == "removed":
        raise authorization_error("not_found")
    if member.status == status:
        return TenantMemberMutationResult(
            await _member_view(command.db, context, member), context.tenant.updated_at
        )
    active_admins = context.active_admin_ids()
    if status == "suspended" and user_id in active_admins and len(active_admins) == 1:
        raise authorization_error("last_tenant_admin")
    member.status = status
    action = (
        audit_actions.TENANT_MEMBER_SUSPEND
        if status == "suspended"
        else audit_actions.TENANT_MEMBER_ADD
    )
    await _commit_member_change(command, context, member, action)
    return TenantMemberMutationResult(
        await _member_view(command.db, context, member), context.tenant.updated_at
    )


async def remove_member(command: TenantMemberWrite, user_id: UUID) -> datetime:
    context = await lock_tenant_member_write(command, lock_children=False)
    try:
        await exit_tenant_member(
            TenantMemberExitRequest(
                command.db, command.tenant_id, user_id, command.actor, command.request
            )
        )
        context.tenant.updated_at = bump_timestamp(context.tenant.updated_at)
        await command.db.commit()
    except SQLAlchemyError:
        await command.db.rollback()
        raise
    return context.tenant.updated_at


async def _commit_member_change(
    command: TenantMemberWrite,
    context: TenantMemberWriteContext,
    member: TenantMember,
    action: str,
) -> None:
    try:
        context.tenant.updated_at = bump_timestamp(context.tenant.updated_at)
        await audit_service.record_event(
            command.db,
            actor_type="user",
            actor_user_id=command.actor.id,
            actor_email_snapshot=command.actor.email,
            owner_user_id=command.actor.id,
            action=action,
            target_type="tenant_member",
            target_id=member.user_id,
            outcome="success",
            request=command.request,
            metadata={"tenant_id": str(command.tenant_id), "status": member.status},
        )
        await command.db.commit()
    except SQLAlchemyError:
        await command.db.rollback()
        raise


async def _member_view(
    db: AsyncSession, context: TenantMemberWriteContext, member: TenantMember
) -> TenantMemberAdminView:
    user = await db.get(User, member.user_id)
    if user is None:
        raise authorization_error("not_found")
    roles = ROLE_ADAPTER.validate_python(
        tuple(row.role for row in context.user_roles(member.user_id))
    )
    return TenantMemberAdminView.from_projection(TenantMemberProjection(member, user, roles))
