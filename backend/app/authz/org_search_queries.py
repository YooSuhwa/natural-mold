"""Search projections never load password/credential data or foreign members."""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.sql import Select

from app.authz.org_administrators import active_org_users
from app.authz.org_control import OrgControlContext
from app.models.organization_group import Group, GroupMember
from app.models.user import User
from app.schemas.org_search import PrincipalSearchQuery


def search_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def user_search_query(
    context: OrgControlContext, query: PrincipalSearchQuery
) -> Select[tuple[UUID, str, str | None, str]]:
    pattern = search_pattern(query.q)
    email_filter = (
        User.email.ilike(pattern, escape="\\")
        if "can_manage_members" in context.permissions
        else func.lower(User.email) == query.q.casefold()
    )
    return (
        select(User.id, User.name, User.display_name, User.email)
        .where(
            User.id.in_(active_org_users(context.organization)),
            or_(
                User.name.ilike(pattern, escape="\\"),
                User.display_name.ilike(pattern, escape="\\"),
                email_filter,
            ),
        )
        .order_by(User.id)
        .limit(20)
    )


def group_search_query(
    context: OrgControlContext, query: PrincipalSearchQuery
) -> Select[tuple[UUID, str, int]]:
    return (
        select(Group.id, Group.name, func.count(GroupMember.user_id))
        .outerjoin(
            GroupMember,
            (
                (GroupMember.group_id == Group.id)
                & (GroupMember.org_id == Group.org_id)
                & (GroupMember.tenant_id == Group.tenant_id)
                & GroupMember.user_id.in_(active_org_users(context.organization))
            ),
        )
        .where(
            Group.org_id == context.organization.id,
            Group.tenant_id == context.organization.tenant_id,
            Group.name.ilike(search_pattern(query.q), escape="\\"),
        )
        .group_by(Group.id, Group.name)
        .order_by(Group.id)
        .limit(20)
    )
