"""Membership events -> ``authz_outbox`` rows in the caller's transaction (plan 7.2.1, 8.6).

Joins, exits, suspensions, role/capability/group-membership changes, group deletion and
organization creation change the tuples their subjects project (``membership_projection``).
The caller captures the subjects before the change and queues the per-key difference
after it, in the same transaction::

    sync = await capture_user(db, tenant_id, user_id, org_id)
    ...  # membership rows change (organization row already locked)
    await sync.queue(db)
    await db.commit()
    await apply_membership_outbox(db, authz)  # 8.6 item 2: best effort, never raises

Deletes follow 8.6: a revocation marker per (organization, user) is recorded first
(``membership`` > ``role`` > ``capability`` > ``group_member``, all organization-wide for the
user) and the deletes are queued with the marker as their source, so the outbox completion
finalizes it once they landed. A tuple of a group principal names every group member; one
of the ``organization:O#member`` principal blocks the organization object (``*``). A company
admin/auditor tuple (``admin from tenant`` governs every organization) records a ``role``
marker in every organization of the company, a company membership one in each organization
the user belonged to; callers hold the tenant lock, then every
organization lock in UUID-byte order. A direct grant dropped by admission (suspension)
finalizes with the marker of its own organization. Writes need none.

Every mode queues and records markers, as ``GrantService`` revocations do (they record
markers and bump the epoch in legacy too): in legacy mode nothing drains the rows.
Organization/company suspension and archive keep their tuples (7.2.1); requests are
refused upstream, so those events queue nothing.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.util import identity_key

from app.authz.grant_completion import MARKERS_TABLE
from app.authz.membership_projection import (
    GroupSubject,
    OrgSubject,
    Projection,
    Subject,
    UserSubject,
    project,
)
from app.authz.outbox import OutboxRun, TupleOp, enqueue
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.revocations import ANY_SUBJECT, RevocationScope, record_revocation
from app.authz.service_core import AuthzDecisions
from app.authz.tuple_types import RelationTuple
from app.models.organization import Organization

SOURCE_TABLE = "membership"
_TRACKED = "authz.membership.outbox"
_ROLES = frozenset({"admin", "builder", "auditor"})
_PRIORITY: tuple[RevocationScope, ...] = (
    "membership",
    "role",
    "capability",
    "group_member",
    "grant",
)
Key = tuple[str, str, str]
MarkerKey = tuple[UUID, str]  # (organization, subject)


def _key(item: RelationTuple) -> Key:
    return (item.user, item.relation, item.object)


def _ref_id(ref: str) -> UUID:
    return UUID(ref.split(":", 1)[1].split("#", 1)[0])


def diff_ops(before: Iterable[RelationTuple], after: Iterable[RelationTuple]) -> list[TupleOp]:
    """Deletes then writes, per key, in a stable order (``replace`` when the key stays)."""
    old = {_key(item): item for item in before}
    new = {_key(item): item for item in after}
    ops = [TupleOp("delete", old[key]) for key in sorted(old.keys() - new.keys())]
    ops += [
        TupleOp("write", new[key], replace=key in old)
        for key in sorted(new)
        if old.get(key) != new[key]
    ]
    return ops


Markers = dict[MarkerKey, tuple[RevocationScope, list[TupleOp]]]


def _scopes(
    item: RelationTuple, before: Projection, tenant_orgs: Sequence[UUID]
) -> list[tuple[UUID, RevocationScope]]:
    """The organizations (and scope) whose decisions a deleted tuple narrows; ``tenant_orgs``
    is every organization of the company when the event is company-wide (role revocation,
    company suspension), else empty."""
    kind = item.object.split(":", 1)[0]
    if kind == "tenant" and item.relation != "member":  # admin/auditor: every organization
        return [(org, "role") for org in tenant_orgs]
    if kind == "tenant":  # company membership: suspension marks all, an exit its own orgs
        return [(org, "membership") for org in tenant_orgs or _member_orgs(item.user, before)]
    if kind == "group" and item.relation == "member":
        org = before.group_orgs.get(_ref_id(item.object))
        return [] if org is None else [(org, "group_member")]
    if item in before.grant_orgs:  # a direct grant dropped by admission (suspension)
        return [(before.grant_orgs[item], "grant")]
    if kind != "organization":
        return []
    if item.relation == "member":
        return [(_ref_id(item.object), "membership")]
    if item.relation in _ROLES:
        return [(_ref_id(item.object), "role")]
    if item.relation.startswith("cap_"):
        return [(_ref_id(item.object), "capability")]
    return []  # ``organization:O org group:G``, ``tenant:T tenant organization:O``


def _member_orgs(user: str, before: Projection) -> list[UUID]:
    found = {
        _ref_id(t.object)
        for t in before.tuples
        if t.user == user and t.relation == "member" and t.object.startswith("organization:")
    }
    return sorted(found, key=lambda value: value.bytes)


def _subjects(item: RelationTuple, before: Projection) -> list[str]:
    if item.user.startswith("user:"):
        return [item.user]
    if item.user.startswith("group:"):  # every member of the group loses the role
        group = item.user.split("#", 1)[0]
        return sorted(t.user for t in before.tuples if t.object == group and t.relation == "member")
    return [ANY_SUBJECT]  # organization:O#member: refuse the organization object


def plan_markers(
    deletes: Sequence[TupleOp], before: Projection, tenant_orgs: Sequence[UUID] = ()
) -> tuple[Markers, list[TupleOp]]:
    """Deletes per (organization, subject) marker with its strongest scope, and the rest.

    A delete narrowing several organizations (company tuples) or subjects (group
    principals) is queued once per marker, so each marker finalizes on its own row.
    """
    markers: Markers = {}
    plain: list[TupleOp] = []
    for op in deletes:
        subjects = _subjects(op.tuple, before)
        targets: list[tuple[UUID, RevocationScope, str]] = [
            (o, sc, sub) for o, sc in _scopes(op.tuple, before, tenant_orgs) for sub in subjects
        ]
        if not targets:
            plain.append(op)
        for org, scope, subject in targets:
            current, rows = markers.get((org, subject), (scope, []))
            strongest = current if _PRIORITY.index(current) <= _PRIORITY.index(scope) else scope
            markers[(org, subject)] = (strongest, [*rows, op])
    return markers, plain


def track(db: AsyncSession, ids: Iterable[UUID]) -> None:
    """Remember queued rows for ``apply_membership_outbox`` after the commit."""
    db.sync_session.info.setdefault(_TRACKED, []).extend(ids)


@dataclass(frozen=True, slots=True)
class MembershipSync:
    tenant_id: UUID
    subjects: tuple[Subject, ...]
    before: Projection

    @classmethod
    async def capture(
        cls,
        db: AsyncSession,
        tenant_id: UUID,
        subjects: Iterable[Subject],
        *,
        now: datetime | None = None,
    ) -> "MembershipSync":
        """Read the subjects' tuples before the change (flushes pending rows)."""
        await db.flush()
        found = tuple(dict.fromkeys(subjects))
        return cls(tenant_id, found, await project(db, found, now=now or datetime.now(UTC)))

    async def queue(
        self, db: AsyncSession, *, now: datetime | None = None, company_wide: bool = False
    ) -> tuple[UUID, ...]:
        """Diff after the change and enqueue (no commit); ids are also ``track``-ed.

        ``company_wide``: a company membership delete (suspension) marks every organization."""
        stamp = now or datetime.now(UTC)
        await db.flush()
        after = await project(db, self.subjects, now=stamp)
        ops = diff_ops(self.before.tuples, after.tuples)
        deletes = [op for op in ops if op.op == "delete"]
        tenant_orgs = await _tenant_orgs(db, self.tenant_id, deletes, company_wide)
        markers, plain = plan_markers(deletes, self.before, tenant_orgs)
        ids: list[UUID] = []
        for (org, subject), (scope, rows) in sorted(markers.items(), key=_marker_order):
            marker = await record_revocation(
                db,
                org_id=org,
                tenant_id=self.tenant_id,
                scope=scope,
                subject=subject,
                object=f"organization:{org}",
            )
            ids += await enqueue(
                db, rows, source_table=MARKERS_TABLE, source_id=marker.id, now=stamp
            )
        for org in {org: None for org, _ in markers}:
            await _reload_version(db, org)
        rest = plain + [op for op in ops if op.op == "write"]
        if rest:
            ids += await enqueue(db, rest, source_table=SOURCE_TABLE, source_id=uuid4(), now=stamp)
        track(db, ids)
        return tuple(ids)


async def _tenant_orgs(
    db: AsyncSession, tenant_id: UUID, deletes: Sequence[TupleOp], company_wide: bool
) -> list[UUID]:
    """Every organization of the company for company-wide deletes (callers hold the tenant
    and organization locks: tenant first, organizations in UUID-byte order)."""
    company = f"tenant:{tenant_id}"
    wide = [op for op in deletes if op.tuple.object == company]
    if not any(company_wide or op.tuple.relation != "member" for op in wide):
        return []
    found = await db.scalars(select(Organization.id).where(Organization.tenant_id == tenant_id))
    return sorted(found, key=lambda value: value.bytes)


async def _reload_version(db: AsyncSession, org_id: UUID) -> None:
    """The marker's epoch bump expired the loaded organization's version columns."""
    loaded = db.identity_map.get(identity_key(Organization, org_id))
    if loaded is not None:
        await db.refresh(loaded, ["updated_at", "authz_epoch"])


def _marker_order(
    item: tuple[MarkerKey, tuple[RevocationScope, list[TupleOp]]],
) -> tuple[bytes, str]:
    (org, subject), _ = item
    return (org.bytes, subject)  # the organization lock order


async def apply_membership_outbox(db: AsyncSession, authz: AuthzDecisions) -> OutboxRun:
    """After commit: write the rows this session queued; legacy has no writer (no-op)."""
    ids = tuple(db.sync_session.info.pop(_TRACKED, ()))
    return await apply_lifecycle(LifecycleChange(ids), authz)


def principal_subject(tenant_id: UUID, org_id: UUID, kind: str, principal_id: UUID) -> Subject:
    """The subject of a role/capability principal (``user``, ``group`` or ``organization``)."""
    match kind:
        case "user":
            return UserSubject(tenant_id, principal_id, frozenset({org_id}))
        case "group":
            return GroupSubject(org_id, principal_id)
        case _:
            return OrgSubject(org_id)


async def queue_created(db: AsyncSession, tenant_id: UUID, subjects: Iterable[Subject]) -> None:
    """A new subject (organization, group) projected nothing before: write its tuples."""
    await MembershipSync(tenant_id, tuple(subjects), Projection()).queue(db)


async def capture_user(
    db: AsyncSession, tenant_id: UUID, user_id: UUID, org_id: UUID | None
) -> MembershipSync:
    """One account in one organization plus its company tuples (``None``: company only)."""
    orgs = frozenset() if org_id is None else frozenset({org_id})
    return await MembershipSync.capture(db, tenant_id, [UserSubject(tenant_id, user_id, orgs)])
