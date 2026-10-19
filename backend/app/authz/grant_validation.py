"""GrantService input checks: stored target scope, principal boundary, model validity.

The target's organization/company is read from its table (``tuple_loaders.resource_scope``),
never taken from the caller. Shares stay inside the organization (D21): an active member,
one of its groups (groups are hard-deleted, so existence is liveness), a resident
non-archived agent or the organization itself.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.tuple_loaders import resource_scope
from app.authz.tuple_rows import GrantRow
from app.authz.tuple_types import TupleRuleError
from app.authz.tuples import tuples_for
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.authz import RELATIONS, RESOURCE_TYPES
from app.models.organization import OrganizationMember
from app.models.organization_group import Group

WRITABLE_KINDS = frozenset({"share", "delegation", "system_fanout"})


@dataclass(frozen=True, slots=True)
class GrantTarget:
    resource_type: str
    resource_id: UUID


@dataclass(frozen=True, slots=True)
class GrantRequest:
    relation: str
    principal_type: str
    principal_id: UUID
    grant_kind: str = "share"
    expires_at: datetime | None = None
    condition_name: str | None = None
    condition_context: dict[str, JsonValue] | None = None
    source_link: dict[str, JsonValue] | None = None


def invalid(reason: str) -> AppError:
    return authorization_error("grant_invalid", {"reason": reason})


def check_request(target: GrantTarget, request: GrantRequest) -> None:
    if target.resource_type not in RESOURCE_TYPES or request.relation not in RELATIONS:
        raise invalid("relation")
    if request.grant_kind not in WRITABLE_KINDS:
        raise invalid("grant_kind")
    if (request.grant_kind == "delegation") != (request.principal_type == "agent"):
        raise invalid("delegation_principal")


async def check_target(db: AsyncSession, ctx: OrgContext, target: GrantTarget) -> None:
    """Unknown objects and objects of another company/organization are hidden (404)."""
    scope = await resource_scope(db, target.resource_type, target.resource_id)
    if (
        scope is None
        or scope.tenant_id not in (None, ctx.tenant_id)
        or scope.org_id not in (None, ctx.org_id)
    ):
        raise authorization_error("not_found")
    if scope.archived:
        raise invalid("archived")


async def check_principal(db: AsyncSession, ctx: OrgContext, request: GrantRequest) -> None:
    principal = request.principal_id
    match request.principal_type:
        case "user":  # FOR SHARE: serializes with a member removal (E-02, FOR UPDATE)
            query = (
                select(OrganizationMember.user_id)
                .where(
                    OrganizationMember.org_id == ctx.org_id,
                    OrganizationMember.user_id == principal,
                    OrganizationMember.status == "active",
                )
                .with_for_update(read=True)
            )
        case "group":
            query = select(Group.id).where(Group.id == principal, Group.org_id == ctx.org_id)
        case "agent":
            query = select(Agent.id).where(
                Agent.id == principal, Agent.org_id == ctx.org_id, Agent.archived_at.is_(None)
            )
        case "organization":
            if principal != ctx.org_id:
                raise invalid("principal")
            return
        case _:
            raise invalid("principal")
    if await db.scalar(query.limit(1)) is None:
        raise invalid("principal")


def desired_row(
    ctx: OrgContext,
    target: GrantTarget,
    request: GrantRequest,
    before: GrantRow | None,
    now: datetime,
) -> GrantRow:
    """The row after the change (``before`` itself when nothing changes).

    Rejected unless the model can store its tuples now (relation, subject type, condition
    parameters; expiry only where the model allows it and still in the future).
    """
    row = GrantRow(
        id=before.id if before is not None else uuid4(),
        org_id=ctx.org_id,
        resource_type=target.resource_type,
        resource_id=target.resource_id,
        relation=request.relation,
        principal_type=request.principal_type,
        principal_id=request.principal_id,
        granted_at=now,
        grant_kind=request.grant_kind,
        condition_name=request.condition_name,
        condition_context=request.condition_context,
        expires_at=request.expires_at,
        tenant_id=ctx.tenant_id,
    )
    try:
        projected = tuples_for(row, now=now)
    except TupleRuleError as error:
        raise invalid(str(error)) from error
    if not projected:
        raise invalid("expired")
    if before is not None and replace(before, granted_at=now) == row:
        return before  # unchanged re-share keeps its original grant time
    return row
