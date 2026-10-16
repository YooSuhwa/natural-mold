"""DB candidate rows for an enforced list page (plan 8.2 ``candidates``, 8.10).

A candidate is a superset that ``AuthzService.filter_visible`` confirms with BatchCheck;
being a candidate grants nothing. For a resource type the caller's candidates are:

* owned: ``owner = me`` in the current organization and company;
* shared: a same-company, non-system row with an active ``resource_grants`` row whose
  principal is me, one of my groups in the current organization, or the current
  organization itself (``allowed_org``/``consumer`` fanout of company rows included);
* platform (``platform=True`` only): system rows with no organization and no company
  (system tools, MCP servers, skills; OpenFGA's fanout decides the organization).

The ownership predicates live here (plan 8.1/8.10) so routers and services stay free
of ``X.user_id == ...`` checks (``scripts/check_authz_predicates.py``).
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.models.authz import ResourceGrant
from app.models.organization_group import GroupMember


def granted_resource_ids(resource_type: str, ctx: OrgContext) -> Select[tuple[UUID]]:
    """Ids of ``resource_type`` objects with an active grant to me, my groups or my org."""
    my_groups = select(GroupMember.group_id).where(
        GroupMember.user_id == ctx.user_id, GroupMember.org_id == ctx.org_id
    )
    return select(ResourceGrant.resource_id).where(
        ResourceGrant.resource_type == resource_type,
        ResourceGrant.state == "active",
        ResourceGrant.tenant_id == ctx.tenant_id,
        or_(
            and_(ResourceGrant.principal_type == "user", ResourceGrant.principal_id == ctx.user_id),
            and_(
                ResourceGrant.principal_type == "organization",
                ResourceGrant.principal_id == ctx.org_id,
            ),
            and_(
                ResourceGrant.principal_type == "group", ResourceGrant.principal_id.in_(my_groups)
            ),
        ),
    )


def _owner(model: Any) -> Any:
    # marketplace_items keeps its owner in ``owner_user_id`` (``user_id`` is a synonym).
    owner = getattr(model, "owner_user_id", None)
    return model.user_id if owner is None else owner


def candidate_predicate(
    model: Any, resource_type: str, ctx: OrgContext, *, platform: bool = False
) -> ColumnElement[bool]:
    """``WHERE`` clause selecting the candidate rows of ``model`` (an ORM class)."""
    system = getattr(model, "is_system", None)
    owned = and_(
        _owner(model) == ctx.user_id,
        model.org_id == ctx.org_id,
        model.tenant_id == ctx.tenant_id,
    )
    shared = and_(
        model.tenant_id == ctx.tenant_id,
        model.id.in_(granted_resource_ids(resource_type, ctx)),
        *(() if system is None else (system.is_(False),)),
    )
    catalog: ColumnElement[bool] = false()
    if platform:
        if system is None:
            raise ValueError(f"{model.__name__} has no platform (is_system) rows")
        catalog = and_(system.is_(True), model.org_id.is_(None), model.tenant_id.is_(None))
    return or_(owned, shared, catalog)


async def candidate_ids(
    db: AsyncSession,
    model: Any,
    resource_type: str,
    ctx: OrgContext,
    *,
    platform: bool = False,
    where: Sequence[ColumnElement[bool]] = (),
    order_by: Sequence[Any] = (),
) -> list[UUID]:
    """Candidate ids (``where`` narrows, e.g. not archived); ``order_by`` then id."""
    query = (
        select(model.id)
        .where(candidate_predicate(model, resource_type, ctx, platform=platform), *where)
        .order_by(*order_by, model.id)
        .execution_options(autoflush=False)
    )
    return list(await db.scalars(query))
