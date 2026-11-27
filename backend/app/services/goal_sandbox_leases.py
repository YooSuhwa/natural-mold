"""Lifecycle transactions for a locally mirrored goal workspace.

The caller authorizes the scope and commits each intent before contacting a
provider. Tokens fence late completions. A worker that loses an execution lease
can never reconnect to that environment to execute another command.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.goal_sandbox import GoalSandbox, GoalSandboxRead

Action = Literal["create", "resume", "connect", "pause", "recover", "delete"]
_WRITER_ACTIONS = {"create", "resume", "connect"}
_OPERATION_STATE = {
    "create": "executing",
    "resume": "executing",
    "connect": "executing",
    "pause": "pausing",
    "recover": "recovering",
    "delete": "deleting",
}
_READABLE = ("ready", "paused", "executing")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REASONS = {
    "unconfirmed_writer",
    "provider_missing",
    "scope_changed",
    "output_conflict",
    "corrupt_output",
}


@dataclass(frozen=True, slots=True)
class SandboxScope:
    tenant_id: UUID
    org_id: UUID
    user_id: UUID
    conversation_id: UUID
    goal_id: UUID
    goal_version: int
    agent_id: UUID
    policy_hash: str
    image: str
    skill_hash: str
    policy_version: int
    skill_id: UUID
    skill_set: tuple[tuple[UUID, str], ...] = ()

    def __post_init__(self) -> None:
        if self.goal_version < 1 or self.policy_version < 1 or not 0 < len(self.image) <= 512:
            raise ValueError("missing goal workspace version or image")
        if not _DIGEST.fullmatch(self.policy_hash) or not _DIGEST.fullmatch(self.skill_hash):
            raise ValueError("invalid goal workspace fingerprint")

        if self.skill_set:
            ordered = tuple(sorted(self.skill_set))
            if (
                (self.skill_id, self.skill_hash) not in ordered
                or len({key for key, _ in ordered}) != len(ordered)
                or any(
                    not isinstance(key, UUID) or not _DIGEST.fullmatch(digest)
                    for key, digest in ordered
                )
            ):
                raise ValueError("invalid approved sandbox skill set")
            object.__setattr__(self, "skill_set", ordered)

    @property
    def approved_skills(self) -> tuple[tuple[UUID, str], ...]:
        return self.skill_set or ((self.skill_id, self.skill_hash),)

    def storage_values(self) -> dict[str, object]:
        """The approved set identifies the environment shared by its calling skills."""
        canonical_id, canonical_hash = self.approved_skills[0]
        return {
            **asdict(self),
            "skill_id": canonical_id,
            "skill_hash": canonical_hash,
            "skill_set": {str(key): digest for key, digest in self.approved_skills},
        }

    @classmethod
    def from_workspace(cls, row: GoalSandbox) -> SandboxScope:
        scope = {f.name: getattr(row, f.name) for f in fields(cls) if f.name != "skill_set"}
        return cls(
            **scope, skill_set=tuple((UUID(key), digest) for key, digest in row.skill_set.items())
        )

    @property
    def fingerprint(self) -> str:
        values = self.storage_values()
        if len(self.approved_skills) == 1:
            # The sole pinned ID and hash already encode a singleton policy.
            del values["skill_set"]
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SandboxClaim:
    scope: SandboxScope
    workspace_id: UUID
    token: UUID
    provider_id: str | None
    action: Action
    manifest: dict[str, str]
    pending_manifest: dict[str, str] | None
    output_run_id: UUID | None = None


def _timestamp(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def _deadline(timestamp: datetime, ttl: timedelta) -> datetime:
    if ttl <= timedelta(0):
        raise ValueError("lease duration must be positive")
    return timestamp + ttl


def _valid_manifest(manifest: dict[str, str]) -> None:
    for name, digest in manifest.items():
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or len(name.encode()) > 255
            or not _DIGEST.fullmatch(digest)
        ):
            raise ValueError("invalid sandbox output receipt")


def _claim(
    row: GoalSandbox, scope: SandboxScope, action: Action, until: datetime, now: datetime
) -> SandboxClaim:
    token = uuid4()
    row.state = _OPERATION_STATE[action]
    row.operation_token = token
    row.operation_until = until
    row.updated_at = now
    row.retry_after = None
    return SandboxClaim(
        scope,
        row.id,
        token,
        row.provider_id,
        action,
        dict(row.receipt),
        None if row.pending_manifest is None else dict(row.pending_manifest),
        row.pending_run_id if action == "recover" else row.output_run_id,
    )


def _settle(row: GoalSandbox, state: str, now: datetime) -> None:
    row.state = state
    row.operation_token = None
    row.operation_until = None
    row.updated_at = now


async def acquire(
    db: AsyncSession, scope: SandboxScope, *, ttl: timedelta, now: datetime | None = None
) -> SandboxClaim | None:
    timestamp = _timestamp(now)
    digest = scope.fingerprint
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    await db.execute(
        insert(GoalSandbox)
        .values(
            **scope.storage_values(),
            id=uuid4(),
            scope_hash=digest,
            state="new",
            receipt={},
            updated_at=timestamp,
            last_used_at=timestamp,
        )
        .on_conflict_do_nothing(
            index_elements=["scope_hash"], index_where=text("state <> 'deleted'")
        )
    )
    row = await db.scalar(
        select(GoalSandbox)
        .where(
            GoalSandbox.tenant_id == scope.tenant_id,
            GoalSandbox.scope_hash == digest,
            GoalSandbox.state != "deleted",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.state not in {"new", "ready", "paused"}:
        return None
    action: Action = (
        "create" if row.state == "new" else "resume" if row.state == "paused" else "connect"
    )
    timestamp = _timestamp(now)
    row.pending_manifest = None
    row.pending_run_id = None
    row.last_used_at = timestamp
    claim = _claim(row, scope, action, _deadline(timestamp, ttl), timestamp)
    await db.flush()
    return claim


async def _owned(
    db: AsyncSession, claim: SandboxClaim, now: datetime | None = None
) -> GoalSandbox | None:
    row = await db.scalar(
        select(GoalSandbox)
        .where(
            GoalSandbox.id == claim.workspace_id,
            GoalSandbox.tenant_id == claim.scope.tenant_id,
            GoalSandbox.scope_hash == claim.scope.fingerprint,
            GoalSandbox.operation_token == claim.token,
            GoalSandbox.state == _OPERATION_STATE[claim.action],
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.operation_until is None or row.operation_until <= _timestamp(now):
        return None
    return row


async def lock_operation(
    db: AsyncSession, claim: SandboxClaim, *, now: datetime | None = None
) -> bool:
    """Hold this provider operation's fencing row through a bounded finalization."""
    return await _owned(db, claim, now) is not None


async def lock_writer(
    db: AsyncSession, claim: SandboxClaim, *, now: datetime | None = None
) -> bool:
    return claim.action in _WRITER_ACTIONS and await lock_operation(db, claim, now=now)


async def bind_provider(
    db: AsyncSession, claim: SandboxClaim, provider_id: str, *, now: datetime | None = None
) -> bool:
    row = await _owned(db, claim, now)
    if (
        row is None
        or claim.action != "create"
        or not 0 < len(provider_id) <= 255
        or row.provider_id not in {None, provider_id}
    ):
        return False
    row.provider_id = provider_id
    await db.flush()
    return True


async def record_outputs_ready(
    db: AsyncSession,
    claim: SandboxClaim,
    *,
    manifest: dict[str, str],
    run_id: UUID | None = None,
    now: datetime | None = None,
) -> bool:
    _valid_manifest(manifest)
    row = await _owned(db, claim, now)
    if (
        row is None
        or claim.action not in _WRITER_ACTIONS
        or row.provider_id is None
        or (
            row.pending_manifest is not None
            and (row.pending_manifest != manifest or row.pending_run_id != run_id)
        )
    ):
        return False
    row.pending_manifest = dict(manifest)
    row.pending_run_id = run_id
    await db.flush()
    return True


async def finish(
    db: AsyncSession, claim: SandboxClaim, *, manifest: dict[str, str], now: datetime | None = None
) -> bool:
    row = await _owned(db, claim, now)
    if row is None or claim.action not in _WRITER_ACTIONS or row.pending_manifest != manifest:
        return False
    row.receipt = dict(manifest)
    row.output_run_id = row.pending_run_id
    row.pending_run_id = None
    row.pending_manifest = None
    row.last_used_at = _timestamp(now)
    _settle(row, "ready", row.last_used_at)
    await db.flush()
    return True


async def abandon(db: AsyncSession, claim: SandboxClaim, *, now: datetime | None = None) -> bool:
    row = await _owned(db, claim, now)
    if row is None or claim.action not in _WRITER_ACTIONS:
        return False
    _settle(row, "needs_recovery", _timestamp(now))
    await db.flush()
    return True


async def claim_maintenance(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    idle_before: datetime,
    expire_before: datetime,
    ttl: timedelta,
    disabled: bool = False,
    now: datetime | None = None,
    org_id: UUID | None = None,
) -> SandboxClaim | None:
    """Claim one ready operation. SKIP LOCKED lets other workers drain other rows."""
    timestamp = _timestamp(now)
    idle_at = _timestamp(idle_before)
    expiry = _timestamp(expire_before)
    if expiry > idle_at:
        raise ValueError("workspace expiry must follow idle suspension")
    cutoff = timestamp if disabled else expiry
    live_read = (
        select(GoalSandboxRead.token)
        .where(
            GoalSandboxRead.workspace_id == GoalSandbox.id,
            GoalSandboxRead.expires_at > timestamp,
        )
        .exists()
    )
    criteria = [
        GoalSandbox.tenant_id == tenant_id,
        ~live_read,
        or_(GoalSandbox.retry_after.is_(None), GoalSandbox.retry_after <= timestamp),
        or_(
            GoalSandbox.state.in_(("needs_recovery", "retiring")),
            and_(
                GoalSandbox.state.in_(("executing", "pausing", "recovering", "deleting")),
                GoalSandbox.operation_until <= timestamp,
            ),
            and_(
                GoalSandbox.state == "ready",
                GoalSandbox.last_used_at <= (cutoff if disabled else idle_at),
            ),
            and_(GoalSandbox.state == "paused", GoalSandbox.last_used_at <= cutoff),
        ),
    ]
    if org_id is not None:
        criteria.append(GoalSandbox.org_id == org_id)
    row = await db.scalar(
        select(GoalSandbox)
        .where(*criteria)
        .order_by(
            GoalSandbox.updated_at,
            GoalSandbox.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    # READ COMMITTED gets a fresh snapshot here. A reader could have committed
    # between the initial predicate evaluation and acquisition of the row lock.
    if (
        await db.scalar(
            select(GoalSandboxRead.token)
            .where(
                GoalSandboxRead.workspace_id == row.id,
                GoalSandboxRead.expires_at > timestamp,
            )
            .limit(1)
        )
        is not None
    ):
        return None
    await db.execute(
        delete(GoalSandboxRead).where(
            GoalSandboxRead.workspace_id == row.id,
            GoalSandboxRead.expires_at <= timestamp,
        )
    )
    action: Action
    if row.state in {"executing", "recovering", "needs_recovery"}:
        action = "recover"
    elif row.state == "ready" and not disabled and row.last_used_at > expiry:
        action = "pause"
    else:
        # An unfinished pause touched no output. Its previous verified copy is
        # authoritative and the provider can safely be deleted after a restart.
        action = "delete"
    scope = SandboxScope.from_workspace(row)
    claim = _claim(row, scope, action, _deadline(timestamp, ttl), timestamp)
    await db.flush()
    return claim


async def complete_maintenance(
    db: AsyncSession,
    claim: SandboxClaim,
    *,
    manifest: dict[str, str] | None = None,
    now: datetime | None = None,
) -> bool:
    row = await _owned(db, claim, now)
    if row is None or claim.action in _WRITER_ACTIONS:
        return False
    timestamp = _timestamp(now)
    if claim.action == "recover":
        if manifest is None or row.pending_manifest != manifest:
            return False
        row.receipt = dict(manifest)
        row.output_run_id = row.pending_run_id
        row.pending_run_id = None
        row.pending_manifest = None
        _settle(row, "retiring", timestamp)
    elif claim.action == "pause":
        _settle(row, "paused", timestamp)
    else:
        row.deleted_at = timestamp
        _settle(row, "deleted", timestamp)
    await db.flush()
    return True


async def fail_maintenance(
    db: AsyncSession, claim: SandboxClaim, *, now: datetime | None = None
) -> bool:
    row = await _owned(db, claim, now)
    if row is None or claim.action in _WRITER_ACTIONS:
        return False
    timestamp = _timestamp(now)
    # An uncertain pause must never be reattached or retried as a new command.
    _settle(row, "needs_recovery" if claim.action == "recover" else "retiring", timestamp)
    row.retry_after = timestamp + timedelta(seconds=30)
    await db.flush()
    return True


async def mark_unresolved(
    db: AsyncSession, claim: SandboxClaim, reason: str, *, now: datetime | None = None
) -> bool:
    if reason not in _REASONS:
        raise ValueError("unknown goal workspace recovery reason")
    row = await _owned(db, claim, now)
    if row is None or claim.action != "recover":
        return False
    row.resolution_reason = reason
    _settle(row, "unresolved", _timestamp(now))
    await db.flush()
    return True


async def retire_erased_scope(
    db: AsyncSession, claim: SandboxClaim, *, now: datetime | None = None
) -> bool:
    """Caller checked that the authoritative source owner/goal has been erased."""
    row = await _owned(db, claim, now)
    if row is None or claim.action != "recover":
        return False
    row.resolution_reason = "scope_erased"
    _settle(row, "retiring", _timestamp(now))
    await db.flush()
    return True


def _reader_scope(
    tenant_id: UUID, org_id: UUID, user_id: UUID, conversation_id: UUID
) -> tuple[ColumnElement[bool], ...]:
    return (
        GoalSandbox.tenant_id == tenant_id,
        GoalSandbox.org_id == org_id,
        GoalSandbox.user_id == user_id,
        GoalSandbox.conversation_id == conversation_id,
    )


async def _pin_reads(
    db: AsyncSession,
    criteria: tuple[ColumnElement[bool], ...],
    ttl: timedelta,
    now: datetime | None,
) -> tuple[UUID, ...]:
    rows = await db.scalars(
        select(GoalSandbox)
        .where(
            *criteria,
            GoalSandbox.state.in_(_READABLE),
        )
        .order_by(GoalSandbox.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    timestamp = _timestamp(now)
    until = _deadline(timestamp, ttl)
    tokens = []
    for row in rows:
        token = uuid4()
        db.add(GoalSandboxRead(workspace_id=row.id, token=token, expires_at=until))
        row.updated_at = timestamp
        row.last_used_at = timestamp
        tokens.append(token)
    await db.flush()
    return tuple(tokens)


async def acquire_read(
    db: AsyncSession, scope: SandboxScope, *, ttl: timedelta, now: datetime | None = None
) -> UUID | None:
    tokens = await _pin_reads(
        db,
        (GoalSandbox.scope_hash == scope.fingerprint, GoalSandbox.tenant_id == scope.tenant_id),
        ttl,
        now,
    )
    return tokens[0] if tokens else None


async def acquire_conversation_reads(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    ttl: timedelta,
    now: datetime | None = None,
) -> tuple[UUID, ...]:
    return await _pin_reads(
        db, _reader_scope(tenant_id, org_id, user_id, conversation_id), ttl, now
    )


async def _update_reads(
    db: AsyncSession,
    criteria: tuple[ColumnElement[bool], ...],
    tokens: tuple[UUID, ...],
    ttl: timedelta | None,
    now: datetime | None,
) -> int:
    if not tokens:
        return 0
    rows = list(
        await db.scalars(
            select(GoalSandbox)
            .where(
                *criteria,
                GoalSandbox.id.in_(
                    select(GoalSandboxRead.workspace_id).where(GoalSandboxRead.token.in_(tokens))
                ),
            )
            .order_by(GoalSandbox.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    timestamp = _timestamp(now)
    predicate = (
        GoalSandboxRead.token.in_(tokens),
        GoalSandboxRead.workspace_id.in_([row.id for row in rows]),
    )
    if ttl is None:
        changed = list(
            await db.scalars(
                delete(GoalSandboxRead).where(*predicate).returning(GoalSandboxRead.workspace_id)
            )
        )
    else:
        changed = list(
            await db.scalars(
                update(GoalSandboxRead)
                .where(*predicate, GoalSandboxRead.expires_at > timestamp)
                .values(
                    expires_at=_deadline(timestamp, ttl),
                )
                .returning(GoalSandboxRead.workspace_id)
            )
        )
    for row in rows:
        if row.id in changed and row.state in _READABLE:
            row.updated_at = timestamp
            row.last_used_at = timestamp
    await db.flush()
    return len(changed)


async def release_read(
    db: AsyncSession, scope: SandboxScope, token: UUID, *, now: datetime | None = None
) -> bool:
    return bool(
        await _update_reads(
            db,
            (GoalSandbox.scope_hash == scope.fingerprint, GoalSandbox.tenant_id == scope.tenant_id),
            (token,),
            None,
            now,
        )
    )


async def release_conversation_reads(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    tokens: tuple[UUID, ...],
    now: datetime | None = None,
) -> int:
    return await _update_reads(
        db, _reader_scope(tenant_id, org_id, user_id, conversation_id), tokens, None, now
    )


async def renew_conversation_reads(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    tokens: tuple[UUID, ...],
    ttl: timedelta,
    now: datetime | None = None,
) -> int:
    return await _update_reads(
        db, _reader_scope(tenant_id, org_id, user_id, conversation_id), tokens, ttl, now
    )
