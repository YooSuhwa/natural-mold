"""Revoke, restore, delete and export memories (memory policy plan 15, 5.7).

* **Revoke** keeps the text so the person can approve it again; runs stop reading it at
  once and the retention sweep deletes it 30 days later.
* **Delete** is real: the row, its revisions and evidence links go; proposals that made it
  or target it lose their text; memories derived from it are marked ``source_deleted``; a
  keyed digest stops an agent from writing the same text again. Conversation history and
  backups are outside this delete, and the receipt says so.
* **Export** returns everything the person owns (records, revisions, proposals) as JSON or
  Markdown.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import (
    MEMORY_EXPORT,
    MEMORY_RECORD_DELETE,
    MEMORY_RECORD_DELETE_BULK,
    MEMORY_RECORD_RESTORE,
    MEMORY_RECORD_REVOKE,
)
from app.authz.memory_agent_access import row_shared_memory_agent_ids
from app.authz.memory_scope import (
    private_memory_proposal_predicate,
    private_memory_record_predicate,
)
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.exceptions import ValidationError
from app.models.memory import (
    MemoryErasure,
    MemoryProposal,
    MemoryRecord,
    MemoryRevision,
    MemorySource,
)
from app.schemas.memory import MemoryDeletionReceipt
from app.services import audit_service, memory_service
from app.services.memory_digest import memory_digest

REVOKED_KEEP_DAYS = 30
KEPT_ELSEWHERE = ["conversation_history", "backups"]
_LIVE = ("active", "superseded", "revoked")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _owned(
    db: AsyncSession, context: OrgContext, memory_id: uuid.UUID, statuses: Sequence[str]
) -> MemoryRecord | None:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    shared = await row_shared_memory_agent_ids(db, MemoryRecord, memory_id, context)
    return await db.scalar(
        select(MemoryRecord)
        .where(
            MemoryRecord.id == memory_id,
            private_memory_record_predicate(context, shared),
            MemoryRecord.status.in_(statuses),
        )
        .execution_options(populate_existing=True, autoflush=False)
    )


async def _audit(db: AsyncSession, context: OrgContext, action: str, **metadata: Any) -> None:
    target = metadata.pop("target", None)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.user_id,
        owner_user_id=context.user_id,
        action=action,
        target_type="memory_record",
        target_id=target,
        target_owner_user_id=context.user_id,
        outcome="success",
        metadata=metadata,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
    )


async def revoke(
    db: AsyncSession, *, context: OrgContext, memory_id: uuid.UUID, reason: str | None
) -> MemoryRecord | None:
    record = await _owned(db, context, memory_id, ("active",))
    if record is None:
        return None
    now = _now()
    record.status, record.revoked_by, record.revoked_at = "revoked", context.user_id, now
    record.revoke_reason = reason
    record.updated_at = now
    await memory_service.record_revision(db, record, "revoke", context.user_id, reason=reason)
    await _audit(db, context, MEMORY_RECORD_REVOKE, target=record.id, scope=record.scope)
    await db.commit()
    await db.refresh(record)
    return record


async def restore(
    db: AsyncSession, *, context: OrgContext, memory_id: uuid.UUID
) -> MemoryRecord | None:
    """Approve a revoked memory again; its text goes through the checks once more (5.7)."""
    record = await _owned(db, context, memory_id, ("revoked",))
    if record is None:
        return None
    checked = memory_service.check_text(record.content)
    shape = memory_service.with_check(memory_service.current_shape(record), checked)
    memory_service.apply_shape(record, shape)
    record.status, record.revoked_by, record.revoked_at, record.revoke_reason = (
        "active",
        None,
        None,
        None,
    )
    now = _now()
    record.approved_by, record.approved_at, record.approval_channel = (
        context.user_id,
        now,
        "direct",
    )
    record.updated_at = now
    await memory_service.record_revision(db, record, "restore", context.user_id)
    await _audit(db, context, MEMORY_RECORD_RESTORE, target=record.id, scope=record.scope)
    await db.commit()
    await db.refresh(record)
    return record


async def list_revisions(
    db: AsyncSession, *, context: OrgContext, memory_id: uuid.UUID
) -> list[MemoryRevision] | None:
    record = await _owned(db, context, memory_id, _LIVE)
    if record is None:
        return None
    rows = await db.scalars(
        select(MemoryRevision)
        .where(MemoryRevision.record_id == record.id)
        .order_by(MemoryRevision.created_at, MemoryRevision.id)
    )
    return list(rows)


async def purge_records(
    db: AsyncSession, records: Iterable[MemoryRecord], *, erase: bool
) -> MemoryDeletionReceipt:
    """Remove ``records`` and every copy of their text this module can reach (no commit).

    ``erase`` (a person's delete) also stores the keyed digest that refuses the same text
    from an agent later; a retention purge does not.
    """
    rows = list(records)
    ids = [row.id for row in rows]
    if not ids:
        return MemoryDeletionReceipt(
            deleted=0,
            revisions_removed=0,
            proposals_cleared=0,
            derived_marked=0,
            kept_elsewhere=KEPT_ELSEWHERE,
        )
    derived_ids = set(
        await db.scalars(
            select(MemorySource.record_id).where(MemorySource.source_record_id.in_(ids))
        )
    ) - set(ids)
    derived = 0
    for row in await db.scalars(select(MemoryRecord).where(MemoryRecord.id.in_(derived_ids))):
        row.flags = {**(row.flags or {}), "source_deleted": True}
        derived += 1
    proposals = list(
        await db.scalars(
            select(MemoryProposal).where(
                or_(MemoryProposal.record_id.in_(ids), MemoryProposal.target_record_id.in_(ids))
            )
        )
    )
    digests = {memory_digest(row.tenant_id, row.content) for row in rows}
    for proposal in await db.scalars(
        select(MemoryProposal).where(
            MemoryProposal.user_id.in_({row.user_id for row in rows}),
            MemoryProposal.status == "pending",
        )
    ):
        if memory_digest(proposal.tenant_id, proposal.content) in digests:
            proposals.append(proposal)  # a pending copy of the same text
    cleared = 0
    for proposal in {id(p): p for p in proposals}.values():
        proposal.content, proposal.reason = "", None
        proposal.flags = {"erased": True}
        if proposal.status == "pending":
            proposal.status, proposal.resolved_at = "rejected", _now()
        cleared += 1
    if erase:
        seen: set[tuple[uuid.UUID, str]] = set()
        for row in rows:
            key = (row.user_id, memory_digest(row.tenant_id, row.content))
            if key not in seen:
                seen.add(key)
                db.add(
                    MemoryErasure(
                        org_id=row.org_id,
                        tenant_id=row.tenant_id,
                        user_id=row.user_id,
                        content_digest=key[1],
                    )
                )
    revisions = int(
        await db.scalar(
            select(func.count())
            .select_from(MemoryRevision)
            .where(MemoryRevision.record_id.in_(ids))
        )
        or 0
    )
    # Explicit deletes: SQLite test databases do not enforce ON DELETE CASCADE.
    await db.execute(delete(MemoryRevision).where(MemoryRevision.record_id.in_(ids)))
    await db.execute(
        delete(MemorySource).where(
            or_(MemorySource.record_id.in_(ids), MemorySource.source_record_id.in_(ids))
        )
    )
    await db.execute(
        update(MemoryRecord)
        .where(MemoryRecord.superseded_by_id.in_(ids))
        .values(superseded_by_id=None)
    )
    await db.execute(delete(MemoryRecord).where(MemoryRecord.id.in_(ids)))
    return MemoryDeletionReceipt(
        deleted=len(ids),
        revisions_removed=revisions,
        proposals_cleared=cleared,
        derived_marked=derived,
        kept_elsewhere=KEPT_ELSEWHERE,
    )


async def delete_one(
    db: AsyncSession, *, context: OrgContext, memory_id: uuid.UUID
) -> MemoryDeletionReceipt | None:
    record = await _owned(db, context, memory_id, (*_LIVE, "deleted"))
    if record is None:
        return None
    scope = record.scope
    receipt = await purge_records(db, [record], erase=True)
    await _audit(
        db,
        context,
        MEMORY_RECORD_DELETE,
        target=memory_id,
        scope=scope,
        revisions_removed=receipt.revisions_removed,
        proposals_cleared=receipt.proposals_cleared,
    )
    await db.commit()
    return receipt


def _scope_filters(context: OrgContext, scope: str, agent_id: uuid.UUID | None) -> list[Any]:
    filters: list[Any] = [
        MemoryRecord.user_id == context.user_id,
        MemoryRecord.tenant_id == context.tenant_id,
    ]
    if scope == "user":
        filters.append(MemoryRecord.scope == "user")
    elif scope == "agent":
        filters.append(MemoryRecord.scope == "agent")
        if agent_id is not None:
            filters.append(MemoryRecord.agent_id == agent_id)
    return filters


async def count_scope(
    db: AsyncSession, *, context: OrgContext, scope: str, agent_id: uuid.UUID | None
) -> int:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MemoryRecord)
            .where(*_scope_filters(context, scope, agent_id))
        )
        or 0
    )


async def delete_scope(
    db: AsyncSession,
    *,
    context: OrgContext,
    scope: Literal["all", "user", "agent"],
    agent_id: uuid.UUID | None,
    confirm_count: int,
) -> MemoryDeletionReceipt:
    """Delete every memory of the person in ``scope``; refuses when the count moved."""
    current = await count_scope(db, context=context, scope=scope, agent_id=agent_id)
    if current != confirm_count:
        raise ValidationError("MEMORY_BULK_COUNT_CHANGED", "삭제할 기억 수가 바뀌었습니다")
    rows = list(
        await db.scalars(select(MemoryRecord).where(*_scope_filters(context, scope, agent_id)))
    )
    receipt = await purge_records(db, rows, erase=True)
    await _audit(
        db,
        context,
        MEMORY_RECORD_DELETE_BULK,
        scope=scope,
        agent_id=str(agent_id) if agent_id else None,
        deleted=receipt.deleted,
        proposals_cleared=receipt.proposals_cleared,
    )
    await db.commit()
    return receipt


def _dump(value: Any) -> Any:
    return (
        value.isoformat()
        if isinstance(value, datetime)
        else str(value)
        if isinstance(value, uuid.UUID)
        else value
    )


_RECORD_FIELDS = (
    "id",
    "scope",
    "agent_id",
    "kind",
    "subject",
    "subject_label",
    "topic",
    "title",
    "content",
    "reason",
    "status",
    "origin",
    "sensitivity",
    "pinned",
    "importance",
    "occurred_start",
    "occurred_end",
    "valid_until",
    "approved_at",
    "approval_channel",
    "revoked_at",
    "revoke_reason",
    "source_conversation_id",
    "created_at",
    "updated_at",
)
_PROPOSAL_FIELDS = (
    "id",
    "scope",
    "agent_id",
    "kind",
    "content",
    "reason",
    "status",
    "origin",
    "action",
    "record_id",
    "created_at",
    "resolved_at",
)


async def export(
    db: AsyncSession,
    *,
    context: OrgContext,
    scope: Literal["all", "user", "agent"] = "all",
    agent_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The person's own memories, their revisions and proposals (정보주체 열람권)."""
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    records = list(
        await db.scalars(
            select(MemoryRecord)
            .where(
                *_scope_filters(context, scope, agent_id),
                private_memory_record_predicate(context, frozenset()),
                MemoryRecord.status.in_(_LIVE),
            )
            .order_by(MemoryRecord.created_at, MemoryRecord.id)
        )
    )
    ids = [record.id for record in records]
    revisions: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if ids:
        for revision in await db.scalars(
            select(MemoryRevision)
            .where(MemoryRevision.record_id.in_(ids))
            .order_by(MemoryRevision.created_at, MemoryRevision.id)
        ):
            revisions.setdefault(revision.record_id, []).append(
                {
                    "action": revision.action,
                    "at": _dump(revision.created_at),
                    "before": revision.before_content,
                    "after": revision.after_content,
                    "reason": revision.reason,
                }
            )
    proposal_filters: list[Any] = [
        MemoryProposal.user_id == context.user_id,
        MemoryProposal.tenant_id == context.tenant_id,
        MemoryProposal.scope.in_(("user", "agent") if scope == "all" else (scope,)),
        private_memory_proposal_predicate(context, frozenset()),
    ]
    if scope == "agent" and agent_id is not None:
        proposal_filters.append(MemoryProposal.agent_id == agent_id)
    proposals = list(
        await db.scalars(
            select(MemoryProposal).where(*proposal_filters).order_by(MemoryProposal.created_at)
        )
    )
    body = {
        "exported_at": _dump(_now()),
        "scope": scope,
        "records": [
            {
                **{name: _dump(getattr(record, name)) for name in _RECORD_FIELDS},
                "revisions": revisions.get(record.id, []),
            }
            for record in records
        ],
        "proposals": [
            {name: _dump(getattr(proposal, name)) for name in _PROPOSAL_FIELDS}
            for proposal in proposals
        ],
    }
    await _audit(
        db,
        context,
        MEMORY_EXPORT,
        scope=scope,
        records=len(records),
        proposals=len(proposals),
    )
    await db.commit()
    return body


_SCOPE_TITLES = {"user": "내 기억 (모든 에이전트)", "agent": "에이전트별 기억"}
_KIND_TITLES = {
    "fact": "사실",
    "preference": "선호",
    "commitment": "약속·결정",
    "experience": "경험",
    "episode": "작업 기록",
    "procedure": "일하는 방법",
}


def export_markdown(body: dict[str, Any]) -> str:
    """A readable copy of ``export``: by scope, then agent, then kind."""
    lines = ["# 내 기억 내보내기", "", f"내보낸 시각: {body['exported_at']}", ""]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in body["records"]:
        key = (record["scope"], record["agent_id"] or "")
        groups.setdefault(key, []).append(record)
    for (scope, agent), records in sorted(groups.items()):
        title = _SCOPE_TITLES.get(scope, scope)
        lines += [f"## {title}" + (f" · 에이전트 {agent}" if agent else ""), ""]
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            by_kind.setdefault(record["kind"], []).append(record)
        for kind, items in by_kind.items():
            lines += [f"### {_KIND_TITLES.get(kind, kind)}", ""]
            for item in items:
                status = "" if item["status"] == "active" else f" ({item['status']})"
                topic = f"[{item['topic']}] " if item["topic"] else ""
                lines.append(f"- {topic}{item['content']}{status}")
            lines.append("")
    pending = [p for p in body["proposals"] if p["status"] == "pending"]
    if pending:
        lines += ["## 승인 대기", ""]
        lines += [f"- {proposal['content']}" for proposal in pending]
        lines.append("")
    return "\n".join(lines)


async def expired_ids(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    tenant_id: uuid.UUID,
    experience_days: int | None,
    now: datetime,
) -> list[uuid.UUID]:
    """Retention (5.7, Q5): experience past its period, revoked past 30 days."""
    from datetime import timedelta

    naive = now.astimezone(UTC).replace(tzinfo=None)
    conditions: list[Any] = [
        (MemoryRecord.status == "revoked")
        & (MemoryRecord.revoked_at < naive - timedelta(days=REVOKED_KEEP_DAYS))
    ]
    if experience_days is not None:
        happened = func.coalesce(MemoryRecord.occurred_end, MemoryRecord.created_at)
        conditions.append(
            (MemoryRecord.kind == "experience")
            & (happened < naive - timedelta(days=experience_days))
        )
    return list(
        await db.scalars(
            select(MemoryRecord.id).where(
                MemoryRecord.org_id == org_id,
                MemoryRecord.tenant_id == tenant_id,
                or_(*conditions),
            )
        )
    )


async def purge_expired(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    tenant_id: uuid.UUID,
    experience_days: int | None,
    now: datetime,
) -> int:
    ids = await expired_ids(
        db, org_id=org_id, tenant_id=tenant_id, experience_days=experience_days, now=now
    )
    removed = 0
    for start in range(0, len(ids), 500):
        chunk = list(
            await db.scalars(
                select(MemoryRecord).where(MemoryRecord.id.in_(ids[start : start + 500]))
            )
        )
        removed += (await purge_records(db, chunk, erase=False)).deleted
        await db.commit()
    return removed


async def activity(db: AsyncSession, *, context: OrgContext, limit: int = 100) -> list[Any]:
    """The person's own ``memory.*`` audit events, newest first (5.9 활동)."""
    from app.models.audit_event import AuditEvent

    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    rows = await db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.actor_user_id == context.user_id,
            AuditEvent.owner_user_id == context.user_id,
            AuditEvent.action.like("memory.%"),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    return list(rows)


__all__ = [
    "activity",
    "KEPT_ELSEWHERE",
    "count_scope",
    "delete_one",
    "delete_scope",
    "export",
    "export_markdown",
    "expired_ids",
    "list_revisions",
    "purge_expired",
    "purge_records",
    "restore",
    "revoke",
]
