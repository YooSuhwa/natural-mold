"""Capability deltas bind targets and preserve all other principals' grants."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.membership_sync import MembershipSync, principal_subject
from app.authz.org_assignment_queries import capability_query
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_principals import PrincipalTarget, validate_principal
from app.authz.principal_names import principal_names
from app.models.organization_group import OrgCapabilityGrant
from app.schemas.org_assignments import (
    CapabilityAssignment,
    CapabilityGrantView,
    GrantDirectoryQuery,
)
from app.schemas.org_groups import DirectoryPage
from app.services.orgs.assignment_common import AssignmentWrite, finish_assignment_write
from app.services.orgs.groups import members_write_context
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.pagination import decode_cursor, encode_cursor
from app.services.orgs.versioning import utc_timestamp


def capability_view(row: OrgCapabilityGrant, name: str | None = None) -> CapabilityGrantView:
    return CapabilityGrantView.model_validate(
        {
            "id": row.id,
            "principal": {"type": row.principal_type, "id": row.principal_id},
            "principal_name": name,
            "capability": row.capability,
            "granted_by": row.granted_by,
            "granted_at": utc_timestamp(row.granted_at),
        }
    )


async def _views(db: AsyncSession, rows: list[OrgCapabilityGrant]) -> list[CapabilityGrantView]:
    names = await principal_names(db, [(row.principal_type, row.principal_id) for row in rows])
    return [capability_view(row, names.get((row.principal_type, row.principal_id))) for row in rows]


async def list_capabilities(
    db: AsyncSession, context: OrgControlContext, query: GrantDirectoryQuery
) -> DirectoryPage[CapabilityGrantView]:
    context = await load_org_control(
        db,
        context.actor,
        OrgControlTarget(
            context.organization.id,
            "can_manage_members",
        ),
    )
    statement = capability_query(context)
    if query.principal_type is not None and query.principal_id is not None:
        statement = statement.where(
            OrgCapabilityGrant.principal_type == query.principal_type,
            OrgCapabilityGrant.principal_id == query.principal_id,
        )
    position = decode_cursor(query.cursor, context.organization.id)
    if position is not None:
        statement = statement.where(OrgCapabilityGrant.id > position)
    rows = list(
        (await db.scalars(statement.order_by(OrgCapabilityGrant.id).limit(query.limit + 1))).all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    return DirectoryPage[CapabilityGrantView](
        items=await _views(db, selected),
        has_more=more,
        next_cursor=encode_cursor(context.organization.id, selected[-1].id) if more else None,
        updated_at=utc_timestamp(context.organization.updated_at),
    )


async def replace_capabilities(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: CapabilityAssignment
) -> DirectoryPage[CapabilityGrantView]:
    context = await members_write_context(mutation, context)
    db = mutation.db
    if payload.capabilities:
        await validate_principal(db, context, PrincipalTarget(payload.principal))
    statement = capability_query(context).where(
        OrgCapabilityGrant.principal_type == payload.principal.type,
        OrgCapabilityGrant.principal_id == payload.principal.id,
    )
    current = list((await db.scalars(statement)).all())
    wanted, existing = set(payload.capabilities), {row.capability for row in current}
    if wanted != existing:
        org = context.organization
        subject = principal_subject(
            org.tenant_id, org.id, payload.principal.type, payload.principal.id
        )
        sync = await MembershipSync.capture(db, org.tenant_id, [subject])
        for row in current:
            if row.capability not in wanted:
                await db.delete(row)
        for capability in payload.capabilities:
            if capability in existing:
                continue
            db.add(
                OrgCapabilityGrant(
                    org_id=context.organization.id,
                    tenant_id=context.organization.tenant_id,
                    principal_type=payload.principal.type,
                    principal_id=payload.principal.id,
                    capability=capability,
                    granted_by=context.actor.id,
                )
            )
        await sync.queue(db)
        await finish_assignment_write(
            mutation,
            context,
            (
                AssignmentWrite(
                    audit_actions.ORG_CAPABILITY_GRANT,
                    payload.principal,
                    tuple(sorted(value for value in wanted if value not in existing)),
                ),
                AssignmentWrite(
                    audit_actions.ORG_CAPABILITY_REVOKE,
                    payload.principal,
                    tuple(sorted(value for value in existing if value not in wanted)),
                ),
            ),
        )
    rows = list((await db.scalars(statement.order_by(OrgCapabilityGrant.id))).all())
    return DirectoryPage[CapabilityGrantView](
        items=await _views(db, rows),
        next_cursor=None,
        has_more=False,
        updated_at=utc_timestamp(context.organization.updated_at),
    )
