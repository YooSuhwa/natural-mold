"""GrantService: share and delegation writes on ``resource_grants`` (plan 8.2, 8.6, 7.2.1).

Every change runs in the caller's transaction: the grant row and its outbox rows are
written together, so a rollback leaves neither. After the caller commits, ``apply``
pushes the rows to OpenFGA at once (best effort); failures stay queued for the C-05
worker. Lock order: the organization row (``record_revocation`` epoch bump, only when
access can shrink), then the object (``lock_object``), then the queue rows.

* Revocation (8.6): marker, ``revoking`` and the per-key deletes commit together; the grant
  becomes ``revoked`` and the marker is finalized once OpenFGA confirmed the deletes.
* An in-place change of an active grant (expiry, run sources, any condition) may narrow
  access, so it records a ``scope=grant`` marker that finalizes when the rewrite lands.
* Expiry (8.6 item 7, C-05 job): ``expire`` closes a grant past ``expires_at`` through the
  same revocation path with a system actor (audit ``grant.expire``).

Policy (who may share what, stage caps, owner self-revocation) is the caller's (E-01).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.grant_completion import (
    GRANTS_TABLE,
    MARKERS_TABLE,
    complete_revocations,
    marker_refs,
)
from app.authz.grant_projection import change_ops, lock_object
from app.authz.grant_validation import (
    GrantRequest,
    GrantTarget,
    check_principal,
    check_request,
    check_target,
    desired_row,
)
from app.authz.org_context import OrgContext
from app.authz.outbox import OutboxRun, Sessions, TupleWriter, apply_now, enqueue
from app.authz.revocations import finalize_revocations, record_revocation
from app.authz.tuple_loaders import grant_row
from app.authz.tuple_rows import GrantRow
from app.models.authz import AuthzRevocation, ResourceGrant
from app.services import audit_service

__all__ = ["GrantChange", "GrantRequest", "GrantService", "GrantTarget"]


@dataclass(frozen=True, slots=True)
class GrantChange:
    grant: ResourceGrant
    outbox_ids: tuple[UUID, ...]


class GrantService:
    def __init__(
        self,
        writer: TupleWriter | None = None,
        sessions: Sessions | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._writer = writer
        self._sessions = sessions
        self._clock = clock

    async def grant(
        self, db: AsyncSession, ctx: OrgContext, target: GrantTarget, request: GrantRequest
    ) -> GrantChange:
        """Insert the active grant, or change the active row of the same key in place."""
        now = self._clock()
        check_request(target, request)
        await check_target(db, ctx, target)
        await check_principal(db, ctx, request)
        existing = await _active(db, target, request)
        if existing is not None and existing.org_id != ctx.org_id:
            raise authorization_error("conflict")  # another organization's grant: never touched
        before = grant_row(existing) if existing is not None else None
        after = desired_row(ctx, target, request, before, now)
        marker = None
        if existing is not None and after is not before:
            marker = await _record(db, existing)
        await lock_object(db, target.resource_type, target.resource_id)
        current = await _active(db, target, request)
        if _identity(current) != _identity(existing) or (
            current is not None and grant_row(current) != before
        ):
            raise authorization_error("conflict")  # changed while we waited for the lock
        grant = current or ResourceGrant(id=after.id, org_id=ctx.org_id, tenant_id=ctx.tenant_id)
        _assign(grant, after, request, ctx.user_id)
        db.add(grant)
        try:
            await db.flush()
        except IntegrityError as error:  # a concurrent first share; the caller rolls back
            raise authorization_error("conflict") from error
        ops = await change_ops(db, before=before, after=after, now=now)
        source = (MARKERS_TABLE, marker.id) if marker else (GRANTS_TABLE, grant.id)
        ids = await enqueue(db, ops, source_table=source[0], source_id=source[1], now=now)
        if marker is not None and not ids:
            await finalize_revocations(db, [marker.id], now=now)
        await _audit(db, ctx.user_id, _grant_action(request, before), grant, before)
        return GrantChange(grant, tuple(ids))

    async def revoke(self, db: AsyncSession, ctx: OrgContext, grant_id: UUID) -> GrantChange:
        """8.6 steps 1-2 in the caller's transaction; step 3 runs when the deletes land."""
        found = await db.get(ResourceGrant, grant_id, populate_existing=True)
        if found is None or found.org_id != ctx.org_id or found.state != "active":
            raise authorization_error("not_found")
        change = await self._close(db, found, ctx.user_id)
        if change is None:
            raise authorization_error("conflict")
        return change

    async def expire(self, db: AsyncSession, grant_id: UUID) -> GrantChange | None:
        """8.6 item 7: close an active grant past ``expires_at`` like a revocation.

        Idempotent across workers: a grant another worker already closed (or that is no
        longer expired) returns ``None`` and the caller rolls back.
        """
        found = await db.get(ResourceGrant, grant_id, populate_existing=True)
        if found is None or not _expired(found, self._clock()):
            return None
        return await self._close(db, found, None)

    async def _close(
        self, db: AsyncSession, found: ResourceGrant, actor: UUID | None
    ) -> GrantChange | None:
        """Marker, ``revoking`` and per-key deletes; ``None`` = closed meanwhile (roll back)."""
        now = self._clock()
        await _record(db, found)
        await lock_object(db, found.resource_type, found.resource_id)
        grant = await db.scalar(
            select(ResourceGrant).where(
                ResourceGrant.id == found.id, ResourceGrant.state == "active"
            )
        )
        if grant is None or (actor is None and not _expired(grant, now)):
            return None
        before = grant_row(grant)
        grant.state = "revoking"
        grant.revoked_by = actor
        await db.flush()
        ops = await change_ops(db, before=before, after=None, now=now)
        ids = await enqueue(db, ops, source_table=GRANTS_TABLE, source_id=grant.id, now=now)
        if not ids:  # another row still projects every key: nothing to wait for
            await complete_revocations(db, [grant.id], now=now)
        delegation = grant.grant_kind == "delegation"
        action = audit_actions.DELEGATION_REVOKE if delegation else audit_actions.GRANT_REVOKE
        await _audit(db, actor, audit_actions.GRANT_EXPIRE if actor is None else action, grant)
        return GrantChange(grant, tuple(ids))

    async def list_grants(
        self, db: AsyncSession, ctx: OrgContext, resource_type: str, resource_id: UUID
    ) -> list[ResourceGrant]:
        """Active and revoking grants of the current organization on one object."""
        found = await db.scalars(
            select(ResourceGrant)
            .where(
                ResourceGrant.org_id == ctx.org_id,
                ResourceGrant.resource_type == resource_type,
                ResourceGrant.resource_id == resource_id,
                ResourceGrant.state.in_(("active", "revoking")),
            )
            .order_by(ResourceGrant.granted_at, ResourceGrant.id)
        )
        return list(found)

    async def apply(self, change: GrantChange) -> OutboxRun:
        """Call after commit (8.6 item 2); never raises, failures stay for the worker."""
        if self._writer is None or self._sessions is None:
            return OutboxRun()
        return await apply_now(self._sessions, self._writer, change.outbox_ids, now=self._clock())


async def _active(
    db: AsyncSession, target: GrantTarget, request: GrantRequest
) -> ResourceGrant | None:
    return await db.scalar(
        select(ResourceGrant)
        .where(
            ResourceGrant.resource_type == target.resource_type,
            ResourceGrant.resource_id == target.resource_id,
            ResourceGrant.relation == request.relation,
            ResourceGrant.principal_type == request.principal_type,
            ResourceGrant.principal_id == request.principal_id,
            ResourceGrant.state == "active",
        )
        .execution_options(populate_existing=True)
    )


def _identity(grant: ResourceGrant | None) -> UUID | None:
    return None if grant is None else grant.id


async def _record(db: AsyncSession, grant: ResourceGrant) -> AuthzRevocation:
    subject, obj = marker_refs(grant)
    return await record_revocation(
        db,
        org_id=grant.org_id,
        tenant_id=grant.tenant_id,
        scope="grant",
        subject=subject,
        object=obj,
    )


def _grant_action(request: GrantRequest, before: GrantRow | None) -> str:
    if request.grant_kind == "delegation":
        return audit_actions.DELEGATION_CREATE
    return audit_actions.GRANT_UPDATE if before is not None else audit_actions.GRANT_CREATE


def _assign(grant: ResourceGrant, row: GrantRow, request: GrantRequest, actor: UUID) -> None:
    grant.state = "active"
    grant.resource_type, grant.resource_id = row.resource_type, row.resource_id
    grant.relation = row.relation
    grant.principal_type, grant.principal_id = row.principal_type, row.principal_id
    grant.grant_kind = row.grant_kind
    grant.condition_name, grant.condition_context = row.condition_name, row.condition_context
    grant.expires_at = row.expires_at
    grant.source_link = request.source_link
    if grant.granted_at != row.granted_at:
        grant.granted_at, grant.granted_by = row.granted_at, actor


def _terms(row: GrantRow | ResourceGrant) -> dict[str, object]:
    expires = row.expires_at.isoformat() if row.expires_at else None
    return {
        "expires_at": expires,
        "condition": row.condition_name,
        "context": row.condition_context,
    }


def _expired(grant: ResourceGrant, now: datetime) -> bool:
    expires = grant.expires_at
    if grant.state != "active" or expires is None:
        return False
    return (expires if expires.tzinfo else expires.replace(tzinfo=UTC)) <= now


async def _audit(
    db: AsyncSession,
    actor: UUID | None,
    action: str,
    grant: ResourceGrant,
    before: GrantRow | None = None,
) -> None:
    metadata: dict[str, object] = {
        "grant_id": str(grant.id),
        "org_id": str(grant.org_id),
        "relation": grant.relation,
        "principal_type": grant.principal_type,
        "principal_id": str(grant.principal_id),
        "grant_kind": grant.grant_kind,
        **_terms(grant),
    }
    if before is not None:
        metadata["before"] = _terms(before)
    await audit_service.record_event(
        db,
        actor_type="user" if actor is not None else "system",
        actor_user_id=actor,
        action=action,
        target_type=grant.resource_type,
        target_id=grant.resource_id,
        outcome="success",
        metadata=metadata,
    )
