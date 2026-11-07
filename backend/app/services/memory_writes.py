"""One path for every memory write (memory policy plan 15, 5.3).

Order: secrets and high-risk personal data (reject) → erased text (reject) → exact
duplicate (keep the existing memory) → the write decision (``memory_policy``) → store a
record or a proposal. The memory screens and the agent's memory tools both come here; only
the origin differs, and the server decides it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.memory_scope import memory_owned_by
from app.authz.org_context import OrgContext
from app.exceptions import ValidationError
from app.models.memory import (
    AgentMemorySettings,
    MemoryErasure,
    MemoryProposal,
    MemoryRecord,
)
from app.models.organization import Tenant
from app.schemas.memory import MemoryProposalCreate, MemoryRecordCreate
from app.services import memory_service
from app.services.memory_checks import content_fingerprint
from app.services.memory_digest import memory_digest
from app.services.memory_policy import (
    decide_record_write,
    effective_mode,
    mode_axes,
    tenant_memory_policy,
)
from app.services.memory_service import MemoryShape, WriteChannel

WriteResultKind = Literal["saved", "proposed", "duplicate", "rejected"]


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    scope: Literal["user", "agent"]
    content: str
    reason: str | None = None
    agent_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    source_run_id: str | None = None
    shape: MemoryShape = MemoryShape()
    run_path: str = "chat"
    channel: WriteChannel = "direct"


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    kind: WriteResultKind
    record: MemoryRecord | None = None
    proposal: MemoryProposal | None = None
    reason: str | None = None
    review_later: bool = False


async def _tenant_settings(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    settings = await db.scalar(select(Tenant.settings).where(Tenant.id == tenant_id))
    return dict(settings or {})


async def tenant_settings_for(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """The tenant's settings (``kind`` and ``memory``) for the policy routes."""
    return await _tenant_settings(db, tenant_id)


async def _erased(db: AsyncSession, context: OrgContext, content: str) -> bool:
    digest = memory_digest(context.tenant_id, content)
    found = await db.scalar(
        select(MemoryErasure.id).where(
            memory_owned_by(MemoryErasure, context),
            MemoryErasure.content_digest == digest,
        )
    )
    return found is not None


async def _forget_erasure(db: AsyncSession, context: OrgContext, content: str) -> None:
    digest = memory_digest(context.tenant_id, content)
    rows = await db.scalars(
        select(MemoryErasure).where(
            memory_owned_by(MemoryErasure, context),
            MemoryErasure.content_digest == digest,
        )
    )
    for row in rows:
        await db.delete(row)


async def _duplicate(
    db: AsyncSession, context: OrgContext, write: MemoryWrite
) -> MemoryRecord | None:
    """An active memory of the same scope with the same normalized text (always safe)."""
    agent = write.agent_id if write.scope == "agent" else None
    rows = await db.scalars(
        select(MemoryRecord).where(
            memory_owned_by(MemoryRecord, context),
            MemoryRecord.scope == write.scope,
            MemoryRecord.agent_id.is_(None) if agent is None else MemoryRecord.agent_id == agent,
            MemoryRecord.status == "active",
        )
    )
    wanted = content_fingerprint(write.content)
    return next((row for row in rows if content_fingerprint(row.content) == wanted), None)


async def _mode(db: AsyncSession, context: OrgContext, agent_id: uuid.UUID | None):
    settings = await memory_service.get_user_settings(db, context.user_id)
    tenant = tenant_memory_policy(await _tenant_settings(db, context.tenant_id))
    mode = effective_mode(
        memory_enabled=settings.memory_enabled,
        approval_mode=settings.approval_mode,
        legacy_write_policy=settings.memory_write_policy,
        tenant=tenant,
    )
    axes = mode_axes(mode, settings.approval_axes)
    if agent_id is not None:
        override = await db.scalar(
            select(AgentMemorySettings.memory_policy_override).where(
                AgentMemorySettings.agent_id == agent_id
            )
        )
        # The agent's override narrows only (5.2 "더 좁게만").
        if override == "off":
            axes["records"] = "off"
        elif override == "ask" and axes["records"] == "auto":
            axes["records"] = "approve"
    return mode, axes, tenant


mode_for = _mode  # the consolidation job reads the same axes (plan 15, 5.6)


async def write_memory(
    db: AsyncSession, *, context: OrgContext, write: MemoryWrite
) -> MemoryWriteResult:
    """Check, decide and store; raises ``ValidationError`` for text that may never be kept."""
    checked = memory_service.check_text(write.content)
    mode, axes, tenant = await _mode(db, context, write.agent_id)
    if "contact" in checked.flags and not tenant.contacts_allowed:
        raise ValidationError("MEMORY_CONTACT_NOT_ALLOWED", "연락처는 기억으로 저장할 수 없습니다")
    if write.shape.origin == "user_stated":
        await _forget_erasure(db, context, write.content)  # the person chose to keep it again
    elif await _erased(db, context, write.content):
        return MemoryWriteResult("rejected", reason="MEMORY_ERASED")
    existing = await _duplicate(db, context, write)
    if existing is not None:
        return MemoryWriteResult("duplicate", record=existing)
    shape = memory_service.with_check(write.shape, checked)
    policy = await memory_service.resolve_effective_policy(
        db, context=context, agent_id=write.agent_id
    )
    decision = decide_record_write(
        mode=mode,
        axes=axes,
        run_path=write.run_path,
        origin=shape.origin,
        flags=[name for name, on in (shape.flags or {}).items() if on],
        trigger_writes_enabled=policy.trigger_write_policy != "off",
    )
    if decision.outcome == "reject":
        return MemoryWriteResult("rejected", reason=decision.reason)
    if decision.outcome == "propose":
        proposal = await memory_service.create_memory_proposal(
            db,
            context=context,
            payload=MemoryProposalCreate(
                scope=write.scope,
                content=write.content,
                reason=write.reason,
                agent_id=write.agent_id,
                conversation_id=write.conversation_id,
                source_run_id=write.source_run_id,
            ),
            channel=write.channel,
            shape=shape,
        )
        if proposal is None:
            return MemoryWriteResult("rejected", reason="agent_not_found")
        return MemoryWriteResult("proposed", proposal=proposal)
    if decision.review_later:
        shape = replace(shape, flags={**(shape.flags or {}), "review_later": True})
    channel = "direct" if shape.origin == "user_stated" else "auto_policy"
    record = await memory_service.create_memory_record(
        db,
        context=context,
        payload=MemoryRecordCreate(
            scope=write.scope,
            content=write.content,
            reason=write.reason,
            agent_id=write.agent_id,
            source_conversation_id=write.conversation_id,
            source_run_id=write.source_run_id,
        ),
        channel=write.channel,
        shape=replace(shape, approval_channel=channel),
        run_path=write.run_path,
    )
    if record is None:
        return MemoryWriteResult("rejected", reason="agent_not_found")
    return MemoryWriteResult("saved", record=record, review_later=decision.review_later)


__all__ = ["MemoryWrite", "MemoryWriteResult", "write_memory"]
