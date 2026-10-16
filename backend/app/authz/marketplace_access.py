"""Marketplace route decisions through ``AuthzService`` (plan 9.6, 10.5, D-05).

Routes build one ``ItemGate`` per decision and hand it to the marketplace services
(``app.marketplace.access.ItemAuthorizer``); legacy mode passes ``None`` instead, so the
existing loaders and ``can_*_item`` predicates answer with the same queries and errors.

* shadow: the service's legacy loader runs, its row and the legacy predicate give the
  verdict, and ``AuthzService.require`` records the sample (raising the legacy error).
* enforce: the gate loads its own candidate (the caller's organization, a platform row or a
  company-public row of the same company) and OpenFGA decides: hidden -> the existing
  ``MARKETPLACE_ITEM_NOT_FOUND``, visible without the permission -> 403 (8.4). Item status
  (draft/disabled) stays a product rule applied after the permission.

Operator bypass (10.5 "운영자 우회"): enforced decisions never consult ``is_super_user``.
The only operator path is ``app.authz.marketplace_moderation.super_moderation``
(``SUPER_MODERATION``): disable/enable (restore) of platform items and the SUPER listing
route's read-back, each recorded as an audit decision.

A ``then`` hook runs only after the item is authorized (install's ``cap_*`` check), so a
missing capability never distinguishes a hidden item from a missing one.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.authz.candidates import candidate_predicate
from app.authz.marketplace_moderation import ModerationAction, super_moderation
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.private_actor import require_private_actor
from app.authz.resource_gate import (
    MCP_SERVER,
    GatedResource,
    gate_cap,
    gate_list,
    gate_unchecked,
)
from app.authz.service import AuthzService, Verdict
from app.dependencies import CurrentUser
from app.error_codes import (
    agent_not_found,
    marketplace_item_not_found,
    marketplace_manage_forbidden,
    skill_not_found,
)
from app.marketplace.access import can_install_item, can_manage_item
from app.models.agent import Agent
from app.models.marketplace import MarketplaceItem as Item
from app.models.mcp_server import McpServer
from app.models.skill import Skill

FGA_TYPE = "marketplace_item"
MIN_VISIBLE = "can_view"
PUBLISH = "cap_publish_marketplace"
LegacyRule = Callable[[Item, CurrentUser], Verdict]
AfterAuthorized = Callable[[Item], Awaitable[None]]

SOURCES: dict[str, GatedResource[Skill] | GatedResource[Agent] | GatedResource[McpServer]] = {
    "skill": GatedResource("skill", Skill, "can_use", skill_not_found),
    "agent": GatedResource("agent", Agent, "can_run", agent_not_found, False),
    "mcp": MCP_SERVER,
}


def view_rule(item: Item, user: CurrentUser) -> Verdict:
    return Verdict.ALLOW  # the legacy view loader already hid what the caller may not see


def install_rule(item: Item, user: CurrentUser) -> Verdict:
    return Verdict.ALLOW if can_install_item(item, user) else Verdict.NOT_FOUND


def manage_rule(item: Item, user: CurrentUser) -> Verdict:
    return Verdict.ALLOW if can_manage_item(item, user) else Verdict.FORBIDDEN


def _owned(ctx: OrgContext) -> ColumnElement[bool]:
    return and_(
        Item.owner_user_id == ctx.user_id,
        Item.org_id == ctx.org_id,
        Item.tenant_id == ctx.tenant_id,
    )


def _platform() -> ColumnElement[bool]:
    return and_(Item.is_system.is_(True), Item.org_id.is_(None), Item.tenant_id.is_(None))


def _reachable(ctx: OrgContext) -> ColumnElement[bool]:
    """Rows a decision can be made about: own org, platform, company-public (E-09)."""
    return or_(
        and_(Item.org_id == ctx.org_id, Item.tenant_id == ctx.tenant_id),
        _platform(),
        and_(Item.tenant_id == ctx.tenant_id, Item.tenant_publication == "approved"),
    )


def item_org(row: Item | None, ctx: OrgContext) -> UUID | None:
    """The organization the decision is made in; ``None`` = missing or foreign (404)."""
    if row is None:
        return None
    if row.org_id is None and row.tenant_id is None and row.is_system:
        return ctx.org_id  # platform: allowed_org/tenant fanout decides
    if row.tenant_id != ctx.tenant_id:
        return None
    if row.org_id == ctx.org_id or row.tenant_publication == "approved":
        return ctx.org_id
    return None


def _status_visible(row: Item, ctx: OrgContext, permission: str) -> bool:
    """Product state the model does not carry (legacy ``can_view/can_install_item``)."""
    owned = (row.owner_user_id, row.org_id, row.tenant_id) == (
        ctx.user_id,
        ctx.org_id,
        ctx.tenant_id,
    )
    if permission == "can_manage":
        return True
    if permission == "can_install" and row.status == "disabled":
        return False
    if row.visibility == "system":
        return row.status != "disabled"
    return owned or row.status == "published"


@dataclass(frozen=True, slots=True)
class ItemGate:
    """``ItemAuthorizer`` for one route decision."""

    db: AsyncSession
    authz: AuthzService
    ctx: OrgContext
    user: CurrentUser
    permission: str
    legacy: LegacyRule
    moderation: ModerationAction | None = None
    then: AfterAuthorized | None = None

    @property
    def enforced(self) -> bool:
        return self.authz.enforces(FGA_TYPE)

    async def _load(self, item_id: UUID, *, scope: ColumnElement[bool] | None) -> Item | None:
        query = select(Item).where(Item.id == item_id)
        if scope is not None:
            query = query.where(scope)
        return await self.db.scalar(
            query.options(
                selectinload(Item.latest_version), selectinload(Item.acl_entries)
            ).execution_options(autoflush=False, populate_existing=True)
        )

    async def __call__(self, item_id: UUID, loaded: Item | None) -> Item:
        if self.enforced:
            return await self._enforce(item_id)
        verdict = Verdict.NOT_FOUND if loaded is None else self.legacy(loaded, self.user)
        target = loaded or await self._load(item_id, scope=_reachable(self.ctx))
        await self._require(item_id, target, verdict)
        assert loaded is not None  # noqa: S101 - require raised the legacy denial
        if self.then is not None:
            await self.then(loaded)
        return loaded

    async def _enforce(self, item_id: UUID) -> Item:
        await require_private_actor(self.db, self.ctx)  # active member of the current org
        if self.moderation is not None:
            record = self.permission == "can_manage"
            moderated = await super_moderation(
                self.db, self.user, item_id, self.moderation, record=record
            )
            if moderated is not None:
                return moderated
        row = await self._load(item_id, scope=_reachable(self.ctx))
        await self._require(item_id, row, Verdict.NOT_FOUND)
        if row is None or not _status_visible(row, self.ctx, self.permission):
            raise marketplace_item_not_found()
        if self.then is not None:
            await self.then(row)
        return row

    async def _require(self, item_id: UUID, row: Item | None, legacy: Verdict) -> None:
        await self.authz.require(
            Principal.for_user(self.ctx),
            self.permission,
            f"{FGA_TYPE}:{item_id}",
            min_visible=MIN_VISIBLE,
            resource_org_id=item_org(row, self.ctx),
            legacy=legacy,
            not_found=marketplace_item_not_found(),
            forbidden=marketplace_manage_forbidden(),
        )


def item_gate(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    user: CurrentUser,
    permission: Literal["can_view", "can_install", "can_manage"],
    *,
    moderation: ModerationAction | None = None,
    then: AfterAuthorized | None = None,
) -> ItemGate | None:
    """``None`` in legacy mode: the services keep their own checks (byte-identical)."""
    if authz.mode == "legacy":
        return None
    rule = {"can_view": view_rule, "can_install": install_rule, "can_manage": manage_rule}
    return ItemGate(db, authz, ctx, user, permission, rule[permission], moderation, then)


@dataclass(frozen=True, slots=True)
class CatalogGate:
    """``ListGate``: ``candidates(marketplace_item)`` confirmed with ``can_view``."""

    authz: AuthzService
    ctx: OrgContext

    @property
    def enforced(self) -> bool:
        return self.authz.enforces(FGA_TYPE)

    def view_predicate(self) -> ColumnElement[bool]:
        shared = candidate_predicate(Item, FGA_TYPE, self.ctx, platform=True)
        company = and_(Item.tenant_id == self.ctx.tenant_id, Item.tenant_publication == "approved")
        return and_(
            or_(shared, company),
            or_(
                _owned(self.ctx),
                and_(Item.status != "disabled", Item.visibility == "system"),
                Item.status == "published",
            ),
        )

    def list_predicate(self, *, public_listed_only: bool) -> ColumnElement[bool]:
        owned = _owned(self.ctx)
        clauses = [or_(owned, Item.visibility != "unlisted")]  # unlisted: direct link only
        if public_listed_only:
            clauses.append(or_(owned, Item.visibility != "public", Item.is_listed.is_(True)))
        return and_(*clauses)

    async def filter(self, rows: Sequence[Item]) -> list[Item]:
        return await gate_list(self.authz, self.ctx, FGA_TYPE, MIN_VISIBLE, rows, lambda r: r.id)


def catalog_gate(authz: AuthzService, ctx: OrgContext) -> CatalogGate | None:
    return None if authz.mode == "legacy" else CatalogGate(authz, ctx)


async def require_publish(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, kind: str, source_id: UUID
) -> None:
    """``CAP(cap_publish_marketplace)`` + the source's ``can_share`` (9.6).

    Legacy allows both (the publish services keep their private owner lookup in every
    mode, so a non-owner sharer still cannot publish until owner-free publishing lands).
    """
    await gate_cap(authz, ctx, PUBLISH)
    await require_source_share(db, authz, ctx, kind, source_id)


async def require_source_share(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, kind: str, source_id: UUID
) -> None:
    """A new version (or item) from ``source_id`` needs ``can_share`` on it (9.6)."""
    await gate_unchecked(authz, db, ctx, SOURCES[kind], source_id, "can_share")
