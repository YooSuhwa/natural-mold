"""Versioned membership changes preserve private history and global user accounts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.org_administrators import active_administrators
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_member_exit import (
    clear_member_assignments,
    pause_member_triggers,
    require_no_owned_resources,
    stop_member_entry_points,
)
from app.authz.org_member_queries import company_member_target, member_for_user, member_query
from app.models.organization import OrganizationMember, OrganizationRoleGrant
from app.schemas.org import OrganizationSettings
from app.schemas.org_groups import DirectoryPage, DirectoryQuery
from app.schemas.org_members import MemberCreate, MemberPatch, MemberView
from app.services import audit_service
from app.services.orgs.groups import members_write_context
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.pagination import decode_cursor, encode_cursor
from app.services.orgs.versioning import bump_version, utc_timestamp


@dataclass(frozen=True, slots=True)
class MemberMutationRequest:
    request: OrgMutationRequest
    user_id: UUID


@dataclass(frozen=True, slots=True)
class MemberWrite:
    member: OrganizationMember
    action: Literal[
        "org.member_add",
        "org.member_suspend",
        "org.member_remove",
    ]


def member_view(
    row: tuple[OrganizationMember, str, str | None, str],
    show_email: bool,
) -> MemberView:
    member, name, display_name, email = row
    return MemberView.model_validate(
        {
            "user_id": member.user_id,
            "display_name": display_name or name,
            "email": email if show_email else None,
            "status": member.status,
            "joined_at": utc_timestamp(member.joined_at),
            "removed_at": utc_timestamp(member.removed_at)
            if member.removed_at is not None
            else None,
        }
    )


async def finish_member_write(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    change: MemberWrite,
) -> None:
    bump_version(context.organization)
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=change.action,
        target_type="organization_member",
        target_id=change.member.user_id,
        outcome="success",
        request=mutation.request,
        metadata={
            "org_id": str(context.organization.id),
            "status": change.member.status,
            "authz_epoch": context.organization.authz_epoch,
        },
    )
    await mutation.db.commit()


async def list_members(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    query: DirectoryQuery,
) -> DirectoryPage[MemberView]:
    db = mutation.db
    context = await load_org_control(db, context.actor, OrgControlTarget(context.organization.id))
    statement = member_query(context)
    position = decode_cursor(query.cursor, context.organization.id)
    if position is not None:
        statement = statement.where(OrganizationMember.user_id > position)
    rows = list(
        (await db.execute(statement.order_by(OrganizationMember.user_id).limit(query.limit + 1)))
        .tuples()
        .all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    return DirectoryPage[MemberView](
        items=[member_view(row, "can_manage_members" in context.permissions) for row in selected],
        next_cursor=encode_cursor(context.organization.id, selected[-1][0].user_id)
        if more
        else None,
        has_more=more,
        updated_at=utc_timestamp(context.organization.updated_at),
    )


async def create_member(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    payload: MemberCreate,
) -> tuple[MemberView, bool]:
    context = await members_write_context(mutation, context)
    db = mutation.db
    await company_member_target(db, context, payload.user_id)
    existing = (await db.execute(member_for_user(context, payload.user_id))).tuples().first()
    created = existing is None
    member = (
        existing[0]
        if existing is not None
        else OrganizationMember(
            org_id=context.organization.id,
            tenant_id=context.organization.tenant_id,
            user_id=payload.user_id,
            status="active",
            invited_by=context.actor.id,
        )
    )
    was_removed = member.status == "removed"
    changed = created or member.status != "active"
    if changed:
        member.status, member.removed_at = "active", None
        db.add(member)
        role = OrganizationSettings.model_validate(
            context.organization.settings
        ).default_member_role
        if role == "builder" and (created or was_removed):
            db.add(
                OrganizationRoleGrant(
                    org_id=context.organization.id,
                    tenant_id=context.organization.tenant_id,
                    principal_type="user",
                    principal_id=payload.user_id,
                    role="builder",
                    granted_by=context.actor.id,
                )
            )
        await finish_member_write(
            mutation, context, MemberWrite(member, audit_actions.ORG_MEMBER_ADD)
        )
    row = (await db.execute(member_for_user(context, payload.user_id))).tuples().one()
    return member_view(row, True), created


async def change_member_status(
    mutation: MemberMutationRequest,
    context: OrgControlContext,
    payload: MemberPatch,
) -> MemberView:
    context = await members_write_context(mutation.request, context)
    db = mutation.request.db
    row = (await db.execute(member_for_user(context, mutation.user_id))).tuples().first()
    if row is None or row[0].status == "removed":
        raise authorization_error("not_found")
    member = row[0]
    if member.status != payload.status:
        administrators = await active_administrators(db, context.organization)
        async with db.begin_nested():
            member.status = payload.status
            await db.flush()
            if administrators and not await active_administrators(db, context.organization):
                raise authorization_error("last_admin")
            if payload.status == "suspended":
                await pause_member_triggers(db, context, mutation.user_id)
        await finish_member_write(
            mutation.request,
            context,
            MemberWrite(
                member,
                audit_actions.ORG_MEMBER_SUSPEND
                if payload.status == "suspended"
                else audit_actions.ORG_MEMBER_ADD,
            ),
        )
    return member_view(row, True)


async def remove_member(mutation: MemberMutationRequest, context: OrgControlContext) -> None:
    context = await members_write_context(mutation.request, context)
    db = mutation.request.db
    row = (await db.execute(member_for_user(context, mutation.user_id))).tuples().first()
    if row is None:
        raise authorization_error("not_found")
    member = row[0]
    if member.status == "removed":
        return
    administrators = await active_administrators(db, context.organization)
    async with db.begin_nested():
        member.status, member.removed_at = "removed", datetime.now(UTC)
        await db.flush()
        if administrators and not await active_administrators(db, context.organization):
            raise authorization_error("last_admin")
        await require_no_owned_resources(db, context, mutation.user_id)
        await clear_member_assignments(db, context, mutation.user_id)
        await stop_member_entry_points(db, context, mutation.user_id)
    await finish_member_write(
        mutation.request, context, MemberWrite(member, audit_actions.ORG_MEMBER_REMOVE)
    )
