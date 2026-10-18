"""Membership-family rows -> the tuples one subject projects (plan 7.2, 7.2.1).

``project`` runs a few subjects' 7.2 source rows through reconcile's ``admitted_rows`` /
``tuples_for`` / ``merge_tuples``: exactly the slice of ``expected_tuples()`` they own.

* ``UserSubject`` (one company): ``user:U`` tuples of tenant membership and roles, every
  organization membership, role, group membership and capability, and the user's direct
  grants (active ones; admission drops them while the membership is not active).
* ``GroupSubject``: ``organization:O org group:G``, each member's ``user:U member group:G``
  and every tuple whose subject is ``group:G#member`` (roles, capabilities, grants).
* ``OrgSubject``: ``tenant:T tenant organization:O`` and ``organization:O#member`` caps.

Agent and model rows are loaded only for grant admission, never projected themselves.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import tuple_rows as rows
from app.authz.tuple_filters import admitted_rows
from app.authz.tuple_loaders import grant_row
from app.authz.tuple_merge import merge_tuples
from app.authz.tuple_types import RelationTuple, TupleRuleError
from app.authz.tuples import tuples_for
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.model import Model
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant


@dataclass(frozen=True, slots=True)
class UserSubject:
    """``orgs``: the organizations whose rows the event changes (under their locks);
    ``None`` = every organization of the company, ``frozenset()`` = company rows only."""

    tenant_id: UUID
    user_id: UUID
    orgs: frozenset[UUID] | None = None


@dataclass(frozen=True, slots=True)
class GroupSubject:
    org_id: UUID
    group_id: UUID


@dataclass(frozen=True, slots=True)
class OrgSubject:
    org_id: UUID


Subject = UserSubject | GroupSubject | OrgSubject


@dataclass(slots=True)
class Projection:
    tuples: set[RelationTuple] = field(default_factory=set)
    group_orgs: dict[UUID, UUID] = field(default_factory=dict)  # group -> organization
    grant_orgs: dict[RelationTuple, UUID] = field(default_factory=dict)  # grant tuple -> org


async def _all(db: AsyncSession, query: Select[Any]) -> list[Any]:
    return list((await db.execute(query)).all())


def _role_select(model: Any, value: Any, kind: str, pid: UUID) -> Select[Any]:
    return select(model.org_id, value, model.principal_type, model.principal_id).where(
        model.principal_type == kind, model.principal_id == pid
    )


async def _grants(db: AsyncSession, kind: str, pid: UUID, *where: Any) -> list[rows.GrantRow]:
    found = await db.scalars(
        select(ResourceGrant).where(
            ResourceGrant.principal_type == kind,
            ResourceGrant.principal_id == pid,
            ResourceGrant.state == "active",
            *where,
        )
    )
    return [grant_row(grant) for grant in found]


async def _admission_rows(
    db: AsyncSession, grants: list[rows.GrantRow]
) -> tuple[tuple[rows.ResourceRow, ...], tuple[rows.ModelRow, ...]]:
    """Agents (residency, archive) and models (visibility) the grants' admission reads."""
    agents = {g.resource_id for g in grants if g.resource_type == "agent"}
    agents |= {g.principal_id for g in grants if g.principal_type == "agent"}
    models = {g.resource_id for g in grants if g.resource_type == "llm_model"}
    found_agents = await _all(
        db,
        select(Agent.id, Agent.org_id, Agent.user_id, Agent.archived_at).where(
            Agent.id.in_(sorted(agents))
        ),
    )
    found_models = await _all(
        db, select(Model.id, Model.is_visible).where(Model.id.in_(sorted(models)))
    )
    return (
        tuple(rows.ResourceRow("agent", i, o, u, archived_at=a) for i, o, u, a in found_agents),
        tuple(rows.ModelRow(i, bool(v)) for i, v in found_models),
    )


async def _user_snapshot(db: AsyncSession, s: UserSubject) -> rows.Snapshot:
    t, u = s.tenant_id, s.user_id
    tm, tr, om = TenantMember, TenantRoleGrant, OrganizationMember
    rg, gm, cap = OrganizationRoleGrant, GroupMember, OrgCapabilityGrant

    def scoped(column: Any) -> tuple[Any, ...]:
        return () if s.orgs is None else (column.in_(sorted(s.orgs)),)

    members = await _all(
        db,
        select(gm.group_id, gm.org_id, gm.user_id).where(
            gm.tenant_id == t, gm.user_id == u, *scoped(gm.org_id)
        ),
    )
    groups = await _all(
        db, select(Group.id, Group.org_id).where(Group.id.in_(sorted({m[0] for m in members})))
    )
    grants = await _grants(
        db, "user", u, ResourceGrant.tenant_id == t, *scoped(ResourceGrant.org_id)
    )
    resources, models = await _admission_rows(db, grants)
    company = (tm.tenant_id == t, tm.user_id == u)
    return rows.Snapshot(
        tenant_members=_rows(
            rows.TenantMemberRow,
            await _all(db, select(tm.tenant_id, tm.user_id, tm.status).where(*company)),
        ),
        tenant_roles=_rows(
            rows.TenantRoleRow,
            await _all(
                db,
                select(tr.tenant_id, tr.user_id, tr.role).where(tr.tenant_id == t, tr.user_id == u),
            ),
        ),
        org_members=_rows(
            rows.OrgMemberRow,
            await _all(
                db,
                select(om.org_id, om.user_id, om.status).where(
                    om.tenant_id == t, om.user_id == u, *scoped(om.org_id)
                ),
            ),
        ),
        org_roles=_rows(
            rows.OrgRoleRow,
            await _all(
                db,
                _role_select(rg, rg.role, "user", u).where(rg.tenant_id == t, *scoped(rg.org_id)),
            ),
        ),
        groups=_rows(rows.GroupRow, groups),
        group_members=_rows(rows.GroupMemberRow, members),
        capabilities=_rows(
            rows.CapabilityRow,
            await _all(
                db,
                _role_select(cap, cap.capability, "user", u).where(
                    cap.tenant_id == t, *scoped(cap.org_id)
                ),
            ),
        ),
        resources=resources,
        models=models,
        grants=tuple(grants),
    )


def _rows[T](make: Callable[..., T], found: Iterable[Any]) -> tuple[T, ...]:
    return tuple(make(*row) for row in found)


async def _group_snapshot(db: AsyncSession, s: GroupSubject) -> rows.Snapshot:
    o, g = s.org_id, s.group_id
    om, gm = OrganizationMember, GroupMember
    rg, cap = OrganizationRoleGrant, OrgCapabilityGrant
    members = await _all(
        db, select(gm.group_id, gm.org_id, gm.user_id).where(gm.org_id == o, gm.group_id == g)
    )
    grants = await _grants(db, "group", g, ResourceGrant.org_id == o)
    resources, models = await _admission_rows(db, grants)
    users = sorted({m[2] for m in members})
    org_members = await _all(
        db, select(om.org_id, om.user_id, om.status).where(om.org_id == o, om.user_id.in_(users))
    )
    groups = await _all(db, select(Group.id, Group.org_id).where(Group.id == g, Group.org_id == o))
    return rows.Snapshot(
        org_members=tuple(rows.OrgMemberRow(*r) for r in org_members),
        org_roles=tuple(
            rows.OrgRoleRow(*r)
            for r in await _all(db, _role_select(rg, rg.role, "group", g).where(rg.org_id == o))
        ),
        groups=tuple(rows.GroupRow(*r) for r in groups),
        group_members=tuple(rows.GroupMemberRow(*r) for r in members),
        capabilities=tuple(
            rows.CapabilityRow(*r)
            for r in await _all(
                db, _role_select(cap, cap.capability, "group", g).where(cap.org_id == o)
            )
        ),
        resources=resources,
        models=models,
        grants=tuple(grants),
    )


async def _org_snapshot(db: AsyncSession, s: OrgSubject) -> rows.Snapshot:
    cap = OrgCapabilityGrant
    found = await _all(
        db,
        select(Organization.id, Organization.tenant_id, Organization.status).where(
            Organization.id == s.org_id
        ),
    )
    capabilities = await _all(
        db,
        _role_select(cap, cap.capability, "organization", s.org_id).where(cap.org_id == s.org_id),
    )
    return rows.Snapshot(
        organizations=tuple(rows.OrganizationRow(*r) for r in found),
        capabilities=tuple(rows.CapabilityRow(*r) for r in capabilities),
    )


def _owned(subject: Subject, item: RelationTuple) -> bool:
    match subject:
        case UserSubject(user_id=u):
            return item.user == f"user:{u}"
        case GroupSubject(group_id=g):
            return item.object == f"group:{g}" or item.user == f"group:{g}#member"
        case OrgSubject(org_id=o):
            return item.object == f"organization:{o}" and item.user.startswith(
                ("tenant:", f"organization:{o}#")
            )


def _safe(row: rows.Row, now: datetime) -> tuple[RelationTuple, ...]:
    try:  # one corrupt row must not block a membership change; reconcile reports it
        return tuples_for(row, now=now)
    except TupleRuleError:
        return ()


def _slice(
    subject: Subject, snapshot: rows.Snapshot, now: datetime, found: Projection
) -> Iterable[RelationTuple]:
    for row in admitted_rows(snapshot):
        if isinstance(row, rows.ResourceRow):
            continue  # admission input only; resource tuples belong to resource lifecycle
        for item in _safe(row, now):
            if not _owned(subject, item):
                continue
            if isinstance(row, rows.GrantRow):
                found.grant_orgs[item] = row.org_id
            yield item


async def project(db: AsyncSession, subjects: Iterable[Subject], *, now: datetime) -> Projection:
    """The merged tuples the subjects own right now (flush pending changes first)."""
    found = Projection()
    collected: list[RelationTuple] = []
    for subject in dict.fromkeys(subjects):
        snapshot = await _snapshot(db, subject)
        found.group_orgs.update({group.id: group.org_id for group in snapshot.groups})
        collected += _slice(subject, snapshot, now, found)
    found.tuples = set(merge_tuples(collected))
    return found


async def _snapshot(db: AsyncSession, subject: Subject) -> rows.Snapshot:
    match subject:
        case UserSubject():
            return await _user_snapshot(db, subject)
        case GroupSubject():
            return await _group_snapshot(db, subject)
        case OrgSubject():
            return await _org_snapshot(db, subject)
