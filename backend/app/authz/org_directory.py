"""Directory entity filters always bind both organization and company identity."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.error_codes import authorization_error
from app.authz.org_control import OrgControlContext
from app.models.organization_group import Group


def group_query(context: OrgControlContext) -> Select[tuple[Group]]:
    return (
        select(Group)
        .where(
            Group.org_id == context.organization.id,
            Group.tenant_id == context.organization.tenant_id,
        )
        .execution_options(populate_existing=True)
    )


async def scoped_group(db: AsyncSession, context: OrgControlContext, group_id: UUID) -> Group:
    group = await db.scalar(group_query(context).where(Group.id == group_id))
    if group is None:
        raise authorization_error("not_found")
    return group
