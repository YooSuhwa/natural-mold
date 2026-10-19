"""D12 revocation markers and the organization decision generation (plan 8.6).

Revocation order: (1) the caller writes the marker with ``record_revocation``
in the same transaction as the source change and commits; (2) the OpenFGA
delete runs (C-04 outbox); (3) once it is confirmed, ``finalize_revocations``
closes the marker. While a marker is unfinalized the deny overlay
(``revocation_overlay``) refuses the related allows, so a failure at any step
ends in denial. Each transaction that records revocations bumps
``organizations.authz_epoch`` once per organization; a decision taken during a
run may be reused only while the epoch is unchanged.

Lock order (for C-04): ``record_revocation`` updates the organization row
first (the epoch bump) and only then inserts the marker. Callers that also lock
grant/member rows of the same organization should record the revocation before
taking those row locks, or take the organization row lock first themselves, so
concurrent revocations always acquire the organization row first.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args
from uuid import UUID

from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.authz import pending_markers as hint
from app.models.authz import AuthzRevocation
from app.models.organization import Organization

RevocationScope = Literal[
    "grant", "membership", "group_member", "role", "capability", "ownership", "resource"
]
SCOPES: frozenset[str] = frozenset(get_args(RevocationScope))
# The subject loses every decision in the organization while one of these is pending.
ORG_WIDE_SCOPES: frozenset[str] = frozenset({"membership", "group_member", "role", "capability"})
# ``scope="resource"`` (deletion/archive) blocks the object for every subject.
ANY_SUBJECT = "*"
_MAX_REF = 100  # authz_revocations.subject/object column width
_BUMPED = "authz.revocations.bumped_orgs"  # session.info: orgs bumped in this transaction
_HOOKED = "authz.revocations.hooked"


@dataclass(frozen=True, slots=True)
class DecisionStamp:
    """The generation a reusable decision was taken under."""

    org_id: UUID
    epoch: int


def _ref(value: str, name: str) -> str:
    if not value or len(value) > _MAX_REF:
        raise ValueError(f"revocation {name} must be 1..{_MAX_REF} characters")
    return value


async def record_revocation(
    db: AsyncSession,
    *,
    org_id: UUID,
    tenant_id: UUID,
    scope: RevocationScope,
    subject: str,
    object: str,  # noqa: A002 - the table column name
) -> AuthzRevocation:
    """Add a marker and bump the epoch inside the caller's transaction (no commit)."""
    if scope not in SCOPES:
        raise ValueError(f"unknown revocation scope: {scope}")
    marker = AuthzRevocation(
        org_id=org_id,
        tenant_id=tenant_id,
        scope=scope,
        subject=_ref(subject, "subject"),
        object=_ref(object, "object"),
    )
    bumped = _transaction_orgs(db)
    if org_id not in bumped:  # organization row first (lock order above)
        await bump_epoch(db, org_id)
        bumped.add(org_id)
    db.add(marker)
    await db.flush()
    hint.pending_markers.note_recorded()  # before commit: never trust a stale zero
    return marker


def _transaction_orgs(db: AsyncSession) -> set[UUID]:
    """Per-root-transaction set of bumped orgs; the commit refreshes the pending hint."""
    sync = db.sync_session
    if not sync.info.get(_HOOKED):
        event.listen(sync, "after_commit", _after_commit)
        event.listen(sync, "after_transaction_end", _after_transaction_end)
        sync.info[_HOOKED] = True
    return sync.info.setdefault(_BUMPED, set())


def _after_commit(session: Session) -> None:
    if session.info.get(_BUMPED):
        hint.pending_markers.note_recorded()  # a full ttl of lookups from commit time


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:  # root commit or rollback; savepoints keep the set
        session.info.pop(_BUMPED, None)


async def bump_epoch(db: AsyncSession, org_id: UUID) -> int:
    """Atomic increment; returns the new generation."""
    epoch = await db.scalar(
        update(Organization)
        .where(Organization.id == org_id)
        .values(authz_epoch=Organization.authz_epoch + 1)
        .returning(Organization.authz_epoch)
    )
    if epoch is None:
        raise LookupError(f"organization {org_id} does not exist")
    return epoch


async def current_epoch(db: AsyncSession, org_id: UUID) -> int:
    epoch = await db.scalar(select(Organization.authz_epoch).where(Organization.id == org_id))
    if epoch is None:
        raise LookupError(f"organization {org_id} does not exist")
    return epoch


async def decision_stamp(db: AsyncSession, org_id: UUID) -> DecisionStamp:
    return DecisionStamp(org_id, await current_epoch(db, org_id))


async def stamp_is_current(db: AsyncSession, stamp: DecisionStamp) -> bool:
    """Reuse a decision only within the same generation; a missing org is stale."""
    epoch = await db.scalar(select(Organization.authz_epoch).where(Organization.id == stamp.org_id))
    return epoch == stamp.epoch


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def mark_fga_deleted(
    db: AsyncSession, ids: Sequence[UUID], *, now: datetime | None = None
) -> int:
    """Record the confirmed OpenFGA delete; the marker still denies until finalized."""
    if not ids:
        return 0
    changed = await db.scalars(
        update(AuthzRevocation)
        .where(AuthzRevocation.id.in_(ids), AuthzRevocation.fga_deleted_at.is_(None))
        .values(fga_deleted_at=_now(now))
        .returning(AuthzRevocation.id)
    )
    return len(changed.all())


async def finalize_revocations(
    db: AsyncSession, ids: Sequence[UUID], *, now: datetime | None = None
) -> int:
    """Close markers after the delete is confirmed (C-04); idempotent, no commit.

    Finalize only after the OpenFGA delete is confirmed *and* visible to checks.
    The OpenFGA server cache is off by default (8.6 item 10); if it is enabled,
    C-04 must confirm with ``HIGHER_CONSISTENCY`` reads (or wait out the cache
    TTL) before finalizing, or a cached allow would outlive the marker.
    """
    if not ids:
        return 0
    stamp = _now(now)
    changed = await db.scalars(
        update(AuthzRevocation)
        .where(AuthzRevocation.id.in_(ids), AuthzRevocation.finalized_at.is_(None))
        .values(
            finalized_at=stamp,
            fga_deleted_at=func.coalesce(AuthzRevocation.fga_deleted_at, stamp),
        )
        .returning(AuthzRevocation.id)
    )
    return len(changed.all())


async def oldest_pending_at(db: AsyncSession) -> datetime | None:
    """Age signal for C-04/C-05 alerting: a stuck marker keeps denying its objects."""
    return await db.scalar(
        select(func.min(AuthzRevocation.created_at)).where(AuthzRevocation.finalized_at.is_(None))
    )


async def count_pending(db: AsyncSession) -> int:
    """Instance-wide unfinalized markers; read by the 1-second process hint."""
    count = await db.scalar(
        select(func.count())
        .select_from(AuthzRevocation)
        .where(AuthzRevocation.finalized_at.is_(None))
    )
    return int(count or 0)
