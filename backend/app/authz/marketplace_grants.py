"""Marketplace visibility/ACL -> ``resource_grants`` (plan 10.5, D-05).

``marketplace_item_acl`` stays the legacy source until I-04; every route that changes an
item's visibility or ACL dual-writes the 10.5 grant set through ``GrantService`` (so the
tuples go through the outbox) in shadow/enforce mode. Legacy mode writes nothing (8.4
byte-identical); ``m85`` copied the state that existed when it ran.

Mapping (same as ``c01_marketplace_acl_grants``):

* private: none (ACL rows included: a manager grant would imply viewer/installer).
* restricted: one user grant per ACL row (view/install/manage -> viewer/installer/manager).
* public, unlisted: ``organization:<item org>`` installer; ``manage`` ACL rows also become
  user manager grants (C-01 deviation: legacy ``can_manage_item`` honours them).
* system: installer fanout to every organization. Platform items have no organization to
  share in, so the routes never write it; the catalog/bootstrap jobs own that fanout.

Grants are matched on (relation, principal) regardless of ``grant_kind``: m85 rows are
``migration``, rows written here are ``share``.
"""

import logging
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.grant_validation import check_principal
from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service_types import AuthzMode
from app.exceptions import AppError
from app.models.authz import ResourceGrant
from app.models.marketplace import MarketplaceItem, MarketplaceItemACL

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "marketplace_item"
ACL_RELATION = {"view": "viewer", "install": "installer", "manage": "manager"}
# Principal kinds this mapping owns on an item; other grants (none today) are left alone.
_MAPPED_PRINCIPALS = ("user", "organization")

type GrantKey = tuple[str, str, UUID]  # (relation, principal_type, principal_id)


def expected_grants(
    visibility: str,
    acl: Iterable[tuple[UUID, str]],
    *,
    org_id: UUID | None,
    fanout_org_ids: Iterable[UUID] = (),
) -> frozenset[GrantKey]:
    """The 10.5 grant set of one item; ``acl`` is ``(user_id, permission)`` rows."""
    rows = list(acl)
    if visibility == "system":
        return frozenset(("installer", "organization", org) for org in fanout_org_ids)
    if visibility == "private":
        return frozenset()
    if org_id is None:
        raise ValueError(f"{visibility} marketplace item has no organization")
    if visibility == "restricted":
        return frozenset((ACL_RELATION[perm], "user", user) for user, perm in rows)
    managers = {("manager", "user", user) for user, perm in rows if perm == "manage"}
    return frozenset({("installer", "organization", org_id), *managers})


async def active_grants(db: AsyncSession, item_id: UUID) -> dict[GrantKey, list[ResourceGrant]]:
    """Every active mapped grant per key (an m85 row and a share row may share a key)."""
    found = await db.scalars(
        select(ResourceGrant)
        .where(
            ResourceGrant.resource_type == RESOURCE_TYPE,
            ResourceGrant.resource_id == item_id,
            ResourceGrant.state == "active",
            ResourceGrant.principal_type.in_(_MAPPED_PRINCIPALS),
        )
        .order_by(ResourceGrant.id)
    )
    keyed: dict[GrantKey, list[ResourceGrant]] = {}
    for grant in found:
        keyed.setdefault((grant.relation, grant.principal_type, grant.principal_id), []).append(
            grant
        )
    return keyed


async def sync_item_grants(
    db: AsyncSession,
    ctx: OrgContext,
    item: MarketplaceItem,
    *,
    mode: AuthzMode,
    service: GrantService | None = None,
) -> LifecycleChange:
    """Make the item's active grants equal its 10.5 set (call after the ACL/visibility
    change is flushed, before commit); ``apply_lifecycle`` the result after commit."""
    if mode == "legacy" or item.is_system or item.org_id != ctx.org_id:
        return LifecycleChange()
    if item.visibility == "system":  # org items cannot be system (PATCH schema rejects it)
        logger.warning("marketplace grant sync skipped: org item %s is 'system'", item.id)
        return LifecycleChange()
    await db.flush()
    acl = await db.execute(
        select(MarketplaceItemACL.user_id, MarketplaceItemACL.permission).where(
            MarketplaceItemACL.item_id == item.id
        )
    )
    want = expected_grants(item.visibility, acl.tuples(), org_id=item.org_id)
    have = await active_grants(db, item.id)
    grants = service or GrantService()
    outbox: list[UUID] = []
    for key in sorted(have.keys() - want, key=str):
        for stale in have[key]:
            outbox += (await grants.revoke(db, ctx, stale.id)).outbox_ids
    target = GrantTarget(RESOURCE_TYPE, item.id)
    for relation, principal_type, principal_id in sorted(want - have.keys(), key=str):
        request = GrantRequest(relation, principal_type, principal_id)
        try:  # shares stay inside the organization (D21); legacy ACL rows may name outsiders
            await check_principal(db, ctx, request)
        except AppError:
            logger.warning(
                "marketplace grant skipped: %s %s:%s is not in org %s (item %s)",
                relation,
                principal_type,
                principal_id,
                ctx.org_id,
                item.id,
            )
            continue
        outbox += (await grants.grant(db, ctx, target, request)).outbox_ids
    return LifecycleChange(tuple(outbox))
