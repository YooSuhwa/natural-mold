"""Explicit query boundaries for organization and private resources."""

from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Mapped
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from app.authz.org_context import OrgContext


class OrgScopedModel(Protocol):
    org_id: Mapped[UUID | None]
    tenant_id: Mapped[UUID | None]


class PrivateScopedModel(OrgScopedModel, Protocol):
    user_id: Mapped[UUID | None]


class OwnedOrgScopedModel(OrgScopedModel, Protocol):
    """Skill/MCP retain required owners even on nullable platform-scope rows."""

    user_id: Mapped[UUID]


class PlatformResourceModel(PrivateScopedModel, Protocol):
    is_system: Mapped[bool]


class OwnedPlatformResourceModel(OwnedOrgScopedModel, Protocol):
    is_system: Mapped[bool]


class RequiredOrgScopedModel(Protocol):
    org_id: Mapped[UUID]
    tenant_id: Mapped[UUID]


class RequiredPrivateScopedModel(RequiredOrgScopedModel, Protocol):
    user_id: Mapped[UUID]


class PrivateParentModel(PrivateScopedModel, Protocol):
    __table__: ClassVar[FromClause]


class RequiredPrivateParentModel(RequiredPrivateScopedModel, Protocol):
    __table__: ClassVar[FromClause]


class OwnedOrgParentModel(OwnedOrgScopedModel, Protocol):
    __table__: ClassVar[FromClause]


@dataclass(frozen=True, slots=True)
class PrivateParent:
    model: type[PrivateParentModel] | type[RequiredPrivateParentModel] | type[OwnedOrgParentModel]
    on: ColumnElement[bool]


def org_scoped[*Row](
    stmt: Select[tuple[*Row]],
    model: type[OrgScopedModel] | type[RequiredOrgScopedModel],
    ctx: OrgContext,
) -> Select[tuple[*Row]]:
    """Limit candidates to one exact org/company pair; this grants no permissions."""
    return stmt.where(
        model.org_id == ctx.org_id, model.tenant_id == ctx.tenant_id
    ).execution_options(autoflush=False)


def private_scoped[*Row](
    stmt: Select[tuple[*Row]],
    model: type[PrivateScopedModel] | type[RequiredPrivateScopedModel] | type[OwnedOrgScopedModel],
    ctx: OrgContext,
) -> Select[tuple[*Row]]:
    """Private rows require the caller AND current org, including for operators."""
    return stmt.where(private_scope_predicate(model, ctx)).execution_options(autoflush=False)


def private_parent_scoped[*Row](
    stmt: Select[tuple[*Row]], parent: PrivateParent, ctx: OrgContext
) -> Select[tuple[*Row]]:
    """Children inherit their private boundary through an explicit parent join."""
    joined = stmt.join(parent.model.__table__, parent.on)
    return private_scoped(joined, parent.model, ctx)


def resource_candidates[*Row](
    stmt: Select[tuple[*Row]],
    model: type[PlatformResourceModel] | type[OwnedPlatformResourceModel],
    ctx: OrgContext,
) -> Select[tuple[*Row]]:
    """Owner/org candidates plus explicit platform rows; grant/catalog checks remain separate."""
    return stmt.where(
        or_(
            and_(
                model.user_id == ctx.user_id,
                model.org_id == ctx.org_id,
                model.tenant_id == ctx.tenant_id,
            ),
            and_(model.is_system.is_(True), model.org_id.is_(None), model.tenant_id.is_(None)),
        )
    ).execution_options(autoflush=False)


def private_scope_predicate(
    model: type[PrivateScopedModel] | type[RequiredPrivateScopedModel] | type[OwnedOrgScopedModel],
    ctx: OrgContext,
) -> ColumnElement[bool]:
    """Compose the same mandatory owner/org/company boundary inside existing queries."""
    return and_(
        model.user_id == ctx.user_id, model.org_id == ctx.org_id, model.tenant_id == ctx.tenant_id
    )
