"""Scoped manual group mutations share the locked organization version."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.grants import GrantService
from app.authz.membership_projection import GroupSubject
from app.authz.membership_sync import MembershipSync, queue_created, track
from app.authz.org_administrators import active_administrators
from app.authz.org_context import OrgContext
from app.authz.org_control import (
    OrgControlContext,
    OrgControlTarget,
    load_org_control,
    load_org_write_control,
)
from app.authz.org_directory import group_query, scoped_group
from app.models.authz import ResourceGrant
from app.models.organization import OrganizationRoleGrant
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.schemas.org_groups import DirectoryPage, DirectoryQuery, GroupCreate, GroupPatch, GroupView
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.pagination import decode_cursor, encode_cursor
from app.services.orgs.versioning import bump_version, require_current_version, utc_timestamp


@dataclass(frozen=True, slots=True)
class GroupMutationRequest:
    request: OrgMutationRequest
    group_id: UUID


@dataclass(frozen=True, slots=True)
class GroupWrite:
    group: Group
    action: Literal[
        "group.create",
        "group.update",
        "group.delete",
        "group.member_add",
        "group.member_remove",
    ]
    details: dict[str, JsonValue] = field(default_factory=dict)


async def members_write_context(
    mutation: OrgMutationRequest, context: OrgControlContext
) -> OrgControlContext:
    with mutation.db.no_autoflush:
        fresh = await load_org_write_control(
            mutation.db,
            context.actor,
            OrgControlTarget(
                context.organization.id,
                "can_manage_members",
            ),
            context.organization.tenant_id,
        )
        require_current_version(fresh.organization, mutation.expected_version)
        return fresh


async def finish_group_write(
    mutation: OrgMutationRequest, context: OrgControlContext, change: GroupWrite
) -> None:
    bump_version(context.organization)
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=change.action,
        target_type="group",
        target_id=change.group.id,
        target_name_snapshot=change.group.name,
        outcome="success",
        request=mutation.request,
        metadata={
            **change.details,
            "org_id": str(context.organization.id),
            "authz_epoch": context.organization.authz_epoch,
        },
    )
    await mutation.db.commit()


async def list_groups(
    db: AsyncSession, context: OrgControlContext, query: DirectoryQuery
) -> DirectoryPage[GroupView]:
    context = await load_org_control(db, context.actor, OrgControlTarget(context.organization.id))
    position = decode_cursor(query.cursor, context.organization.id)
    statement = group_query(context)
    if position is not None:
        statement = statement.where(Group.id > position)
    rows = list((await db.scalars(statement.order_by(Group.id).limit(query.limit + 1))).all())
    more = len(rows) > query.limit
    selected = rows[: query.limit]
    return DirectoryPage[GroupView](
        items=[GroupView.model_validate(group) for group in selected],
        next_cursor=encode_cursor(context.organization.id, selected[-1].id) if more else None,
        has_more=more,
        updated_at=utc_timestamp(context.organization.updated_at),
    )


async def get_group(db: AsyncSession, context: OrgControlContext, group_id: UUID) -> GroupView:
    context = await load_org_control(db, context.actor, OrgControlTarget(context.organization.id))
    return GroupView.model_validate(await scoped_group(db, context, group_id))


async def create_group(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: GroupCreate
) -> GroupView:
    with mutation.db.no_autoflush:
        context = await members_write_context(mutation, context)
        if (
            await mutation.db.scalar(group_query(context).where(Group.name == payload.name))
            is not None
        ):
            raise authorization_error("conflict")
    group = Group(
        org_id=context.organization.id,
        tenant_id=context.organization.tenant_id,
        name=payload.name,
        description=payload.description,
        source="manual",
        created_by=context.actor.id,
    )
    mutation.db.add(group)
    await mutation.db.flush()
    org = context.organization
    await queue_created(mutation.db, org.tenant_id, [GroupSubject(org.id, group.id)])
    await finish_group_write(mutation, context, GroupWrite(group, audit_actions.GROUP_CREATE))
    return GroupView.model_validate(group)


async def update_group(
    mutation: GroupMutationRequest, context: OrgControlContext, payload: GroupPatch
) -> GroupView:
    db = mutation.request.db
    with db.no_autoflush:
        context = await members_write_context(mutation.request, context)
        group = await scoped_group(db, context, mutation.group_id)
        if group.source != "manual":
            raise authorization_error("policy_locked")
        name = payload.name if payload.name is not None else group.name
        description = (
            payload.description if "description" in payload.model_fields_set else group.description
        )
        if name == group.name and description == group.description:
            return GroupView.model_validate(group)
        duplicate = await db.scalar(
            group_query(context).where(Group.name == name, Group.id != group.id)
        )
        if duplicate is not None:
            raise authorization_error("conflict")
    group.name, group.description, group.updated_at = name, description, datetime.now(UTC)
    await finish_group_write(
        mutation.request, context, GroupWrite(group, audit_actions.GROUP_UPDATE)
    )
    return GroupView.model_validate(group)


async def delete_group(mutation: GroupMutationRequest, context: OrgControlContext) -> None:
    db = mutation.request.db
    with db.no_autoflush:
        context = await members_write_context(mutation.request, context)
        group = await scoped_group(db, context, mutation.group_id)
        if group.source != "manual":
            raise authorization_error("policy_locked")
        administrators = await active_administrators(db, context.organization)
    org = context.organization
    await revoke_group_grants(db, context, group)  # before the capture: GrantService owns them
    sync = await MembershipSync.capture(db, org.tenant_id, [GroupSubject(org.id, group.id)])
    async with db.begin_nested():
        await delete_group_links(db, context, group)
        await db.delete(group)
        await db.flush()
        if administrators and not await active_administrators(db, context.organization):
            raise authorization_error("last_admin")
    await sync.queue(db)
    await finish_group_write(
        mutation.request, context, GroupWrite(group, audit_actions.GROUP_DELETE)
    )


async def revoke_group_grants(db: AsyncSession, context: OrgControlContext, group: Group) -> None:
    """7.2.1 "그룹 삭제": grants naming the group are revoked like any revoke (8.6 markers,
    outbox, ``grant.revoke`` audit with reason ``group_deleted``); applied after commit."""
    org = context.organization
    ctx = OrgContext(org.id, org.tenant_id, context.actor.id, context.actor.is_super_user)
    held = await db.scalars(
        select(ResourceGrant.id)
        .where(
            ResourceGrant.org_id == org.id,
            ResourceGrant.principal_type == "group",
            ResourceGrant.principal_id == group.id,
            ResourceGrant.state == "active",
        )
        .order_by(ResourceGrant.id)
    )
    service = GrantService()
    audit = {"reason": "group_deleted", "group_id": str(group.id)}
    for grant_id in list(held):
        track(db, (await service.revoke(db, ctx, grant_id, audit=audit)).outbox_ids)


async def delete_group_links(db: AsyncSession, context: OrgControlContext, group: Group) -> None:
    await db.execute(
        delete(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == context.organization.id,
            OrganizationRoleGrant.principal_type == "group",
            OrganizationRoleGrant.principal_id == group.id,
        )
    )
    await db.execute(
        delete(OrgCapabilityGrant).where(
            OrgCapabilityGrant.org_id == context.organization.id,
            OrgCapabilityGrant.principal_type == "group",
            OrgCapabilityGrant.principal_id == group.id,
        )
    )
    await db.execute(
        delete(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.org_id == context.organization.id,
        )
    )
