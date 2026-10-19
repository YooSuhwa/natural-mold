"""``my_permissions`` for marketplace items (plan 13.1, 13.9; H-05 + D-05 vocabulary).

``GET /api/resources/marketplace_item/permissions?ids=…`` answers ``can_view``,
``can_install`` and ``can_manage`` for the items among ``ids`` the caller may see; every
other id (hidden, foreign, missing) is left out, so the answer never reveals a row (8.10).

* legacy (and shadow): the catalog's own candidates (``catalog_view_candidate_predicate``)
  and the legacy predicates answer — the owner (or a ``manage`` ACL / operator) manages,
  every viewer may install unless the item is disabled, restricted without an install
  ACL or someone else's private draft (``can_install_item``). Shadow samples the first
  rows only, like the other resource types.
* enforce: the catalog's candidates (``CatalogGate.view_predicate``) confirmed with
  ``can_view`` and one BatchCheck for the three permissions; a disabled item is never
  installable (the D-05 status rule). An outage is 503 ``authz_unavailable``.
"""

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.authz import denials
from app.authz.catalog_scope import catalog_view_candidate_predicate
from app.authz.client import AuthzUnavailable
from app.authz.marketplace_access import FGA_TYPE, MIN_VISIBLE, CatalogGate
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.private_actor import require_private_actor
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.marketplace.access import can_install_item, can_manage_item, can_view_item
from app.models.marketplace import MarketplaceItem as Item

PERMISSIONS: Final = ("can_view", "can_install", "can_manage")
SHADOW_SAMPLE_ROWS: Final = 3


def legacy_item_permissions(item: Item, user: CurrentUser) -> list[str]:
    if not can_view_item(item, user):
        return []
    held = {
        "can_view": True,
        "can_install": can_install_item(item, user),
        "can_manage": can_manage_item(item, user),
    }
    return [perm for perm in PERMISSIONS if held[perm]]


def _obj(item: Item) -> str:
    return f"{FGA_TYPE}:{item.id}"


async def _load(
    db: AsyncSession, ids: Sequence[UUID], scope: ColumnElement[bool]
) -> dict[UUID, Item]:
    found = await db.scalars(
        select(Item)
        .where(Item.id.in_(list(ids)), scope)
        .options(selectinload(Item.acl_entries))
        .execution_options(populate_existing=True)
    )
    return {row.id: row for row in found}


async def marketplace_item_permissions(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    user: CurrentUser,
    ids: Sequence[UUID],
) -> list[tuple[UUID, list[str]]]:
    """``(id, my_permissions)`` for the visible items among ``ids``, in request order."""
    await require_private_actor(db, ctx)  # like the catalog list and ItemGate, every mode
    unique = list(dict.fromkeys(ids))
    if not unique:
        return []
    p = Principal.for_user(ctx)
    if not authz.enforces(FGA_TYPE):
        rows = await _load(db, unique, catalog_view_candidate_predicate(ctx))
        ordered = [rows[i] for i in unique if i in rows]
        answered = [(row, legacy_item_permissions(row, user)) for row in ordered]
        await _shadow(authz, p, answered)
        return [(row.id, held) for row, held in answered if held]
    gate = CatalogGate(authz, ctx)
    rows = await _load(db, unique, gate.view_predicate())
    visible = await gate.filter([rows[i] for i in unique if i in rows])
    if not visible:
        return []
    try:
        answers = await authz.batch_check(
            p, [(perm, _obj(r)) for r in visible for perm in PERMISSIONS]
        )
    except AuthzUnavailable as error:
        raise denials.authz_unavailable() from error

    def held(row: Item) -> list[str]:
        disabled = row.status == "disabled"
        return [
            perm
            for perm in PERMISSIONS
            if answers[(perm, _obj(row))] and not (disabled and perm == "can_install")
        ]

    return [(row.id, held(row)) for row in visible]


async def _shadow(
    authz: AuthzService, p: Principal, answered: Sequence[tuple[Item, list[str]]]
) -> None:
    sample = answered[:SHADOW_SAMPLE_ROWS]
    legacy = [_obj(row) for row, held in sample if held]
    await authz.filter_visible(p, MIN_VISIBLE, [_obj(row) for row, _ in sample], legacy=legacy)
    for row, held in [pair for pair in answered if pair[1]][:SHADOW_SAMPLE_ROWS]:
        await authz.my_permissions(p, _obj(row), list(PERMISSIONS), legacy=held)
