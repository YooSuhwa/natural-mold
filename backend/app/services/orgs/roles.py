"""Role deltas preserve unrelated principals and the last effective administrator."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.org_administrators import active_administrators
from app.authz.org_assignment_queries import role_query
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_principals import PrincipalTarget, validate_principal
from app.models.organization import OrganizationRoleGrant
from app.schemas.org_assignments import RoleAssignment, RoleGrantView
from app.schemas.org_groups import DirectoryPage, DirectoryQuery
from app.services.orgs.assignment_common import AssignmentWrite, finish_assignment_write
from app.services.orgs.groups import members_write_context
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.pagination import decode_cursor, encode_cursor
from app.services.orgs.versioning import utc_timestamp


def role_view(row: OrganizationRoleGrant) -> RoleGrantView:
    return RoleGrantView.model_validate(
        {
            "id": row.id,
            "principal": {"type": row.principal_type, "id": row.principal_id},
            "role": row.role,
            "granted_by": row.granted_by,
            "granted_at": utc_timestamp(row.granted_at),
        }
    )


async def list_roles(
    db: AsyncSession, context: OrgControlContext, query: DirectoryQuery
) -> DirectoryPage[RoleGrantView]:
    context = await load_org_control(
        db,
        context.actor,
        OrgControlTarget(
            context.organization.id,
            "can_manage_members",
        ),
    )
    statement = role_query(context)
    position = decode_cursor(query.cursor, context.organization.id)
    if position is not None:
        statement = statement.where(OrganizationRoleGrant.id > position)
    rows = list(
        (
            await db.scalars(statement.order_by(OrganizationRoleGrant.id).limit(query.limit + 1))
        ).all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    return DirectoryPage[RoleGrantView](
        items=[role_view(row) for row in selected],
        has_more=more,
        next_cursor=encode_cursor(context.organization.id, selected[-1].id) if more else None,
        updated_at=utc_timestamp(context.organization.updated_at),
    )


async def replace_roles(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: RoleAssignment
) -> DirectoryPage[RoleGrantView]:
    db = mutation.db
    with db.no_autoflush:
        context = await members_write_context(mutation, context)
        if payload.roles:
            await validate_principal(
                db,
                context,
                PrincipalTarget(
                    payload.principal,
                    auditor_only=set(payload.roles) == {"auditor"},
                ),
            )
        statement = role_query(context).where(
            OrganizationRoleGrant.principal_type == payload.principal.type,
            OrganizationRoleGrant.principal_id == payload.principal.id,
        )
        current = list(
            (await db.scalars(statement.order_by(OrganizationRoleGrant.id).with_for_update())).all()
        )
        wanted = set(payload.roles)
        existing = {row.role for row in current}
        administrators = (
            await active_administrators(db, context.organization) if wanted != existing else set()
        )
    if wanted != existing:
        async with db.begin_nested():
            for row in current:
                if row.role not in wanted:
                    await db.delete(row)
            for role in payload.roles:
                if role in existing:
                    continue
                db.add(
                    OrganizationRoleGrant(
                        org_id=context.organization.id,
                        tenant_id=context.organization.tenant_id,
                        principal_type=payload.principal.type,
                        principal_id=payload.principal.id,
                        role=role,
                        granted_by=context.actor.id,
                    )
                )
            await db.flush()
            if administrators and not await active_administrators(db, context.organization):
                raise authorization_error("last_admin")
        await finish_assignment_write(
            mutation,
            context,
            (
                AssignmentWrite(
                    audit_actions.ORG_ROLE_GRANT,
                    payload.principal,
                    tuple(sorted(role for role in wanted if role not in existing)),
                ),
                AssignmentWrite(
                    audit_actions.ORG_ROLE_REVOKE,
                    payload.principal,
                    tuple(sorted(role for role in existing if role not in wanted)),
                ),
            ),
        )
    rows = list((await db.scalars(statement.order_by(OrganizationRoleGrant.id))).all())
    return DirectoryPage[RoleGrantView](
        items=[role_view(row) for row in rows],
        next_cursor=None,
        has_more=False,
        updated_at=utc_timestamp(context.organization.updated_at),
    )
