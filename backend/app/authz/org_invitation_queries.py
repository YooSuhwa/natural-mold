"""Invitation rows are scoped to both org and company before token disclosure."""

from sqlalchemy import select
from sqlalchemy.sql import Select

from app.authz.org_control import OrgControlContext
from app.models.organization_group import OrgInvitation


def invitation_query(context: OrgControlContext) -> Select[tuple[OrgInvitation]]:
    return select(OrgInvitation).where(
        OrgInvitation.org_id == context.organization.id,
        OrgInvitation.tenant_id == context.organization.tenant_id,
    )
