"""Manual group membership deltas are scoped and preserve effective administrators."""

from datetime import UTC, datetime
from uuid import UUID

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.org_administrators import active_administrators
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_directory import scoped_group
from app.authz.org_group_member_queries import group_member_query, group_user_query
from app.authz.org_principals import PrincipalTarget, validate_principal
from app.models.organization_group import GroupMember
from app.schemas.org_assignments import UserPrincipal
from app.schemas.org_groups import DirectoryPage, DirectoryQuery, GroupMemberCreate, GroupMemberView
from app.services.orgs.groups import (
    GroupMutationRequest,
    GroupWrite,
    finish_group_write,
    members_write_context,
)
from app.services.orgs.pagination import decode_cursor, encode_cursor
from app.services.orgs.versioning import utc_timestamp


def member_view(
    row: tuple[GroupMember, str, str | None, str],
    show_email: bool,
) -> GroupMemberView:
    member, name, display_name, email = row
    return GroupMemberView(
        user_id=member.user_id,
        display_name=display_name or name,
        email=email if show_email else None,
        added_at=utc_timestamp(member.added_at),
    )


async def list_group_members(
    mutation: GroupMutationRequest,
    context: OrgControlContext,
    query: DirectoryQuery,
) -> DirectoryPage[GroupMemberView]:
    db = mutation.request.db
    context = await load_org_control(db, context.actor, OrgControlTarget(context.organization.id))
    await scoped_group(db, context, mutation.group_id)
    statement = group_member_query(context).where(GroupMember.group_id == mutation.group_id)
    position = decode_cursor(query.cursor, context.organization.id)
    if position is not None:
        statement = statement.where(GroupMember.user_id > position)
    rows = list(
        (await db.execute(statement.order_by(GroupMember.user_id).limit(query.limit + 1)))
        .tuples()
        .all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    return DirectoryPage[GroupMemberView](
        items=[member_view(row, "can_manage_members" in context.permissions) for row in selected],
        has_more=more,
        next_cursor=encode_cursor(context.organization.id, selected[-1][0].user_id)
        if more
        else None,
        updated_at=utc_timestamp(context.organization.updated_at),
    )


async def add_group_member(
    mutation: GroupMutationRequest,
    context: OrgControlContext,
    payload: GroupMemberCreate,
) -> tuple[GroupMemberView, bool]:
    context = await members_write_context(mutation.request, context)
    db = mutation.request.db
    group = await scoped_group(db, context, mutation.group_id)
    if group.source != "manual":
        raise authorization_error("policy_locked")
    await validate_principal(
        db, context, PrincipalTarget(UserPrincipal(type="user", id=payload.user_id))
    )
    existing = await db.get(GroupMember, (group.id, payload.user_id))
    created = existing is None
    if created:
        db.add(
            GroupMember(
                group_id=group.id,
                user_id=payload.user_id,
                org_id=context.organization.id,
                tenant_id=context.organization.tenant_id,
                added_by=context.actor.id,
            )
        )
        group.updated_at = datetime.now(UTC)
        await finish_group_write(
            mutation.request,
            context,
            GroupWrite(
                group,
                audit_actions.GROUP_MEMBER_ADD,
                {"user_id": str(payload.user_id)},
            ),
        )
    statement = group_user_query(context, group.id, payload.user_id)
    row = (await db.execute(statement)).tuples().one()
    return member_view(row, True), created


async def remove_group_member(
    mutation: GroupMutationRequest,
    context: OrgControlContext,
    user_id: UUID,
) -> None:
    context = await members_write_context(mutation.request, context)
    db = mutation.request.db
    group = await scoped_group(db, context, mutation.group_id)
    if group.source != "manual":
        raise authorization_error("policy_locked")
    member = await db.get(GroupMember, (group.id, user_id))
    if (
        member is None
        or member.org_id != context.organization.id
        or member.tenant_id != context.organization.tenant_id
    ):
        raise authorization_error("not_found")
    administrators = await active_administrators(db, context.organization)
    async with db.begin_nested():
        await db.delete(member)
        await db.flush()
        if administrators and not await active_administrators(db, context.organization):
            raise authorization_error("last_admin")
    group.updated_at = datetime.now(UTC)
    await finish_group_write(
        mutation.request,
        context,
        GroupWrite(
            group,
            audit_actions.GROUP_MEMBER_REMOVE,
            {"user_id": str(user_id)},
        ),
    )
