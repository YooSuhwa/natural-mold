"""Shared operating memo of an agent (``/agents/{id}/AGENTS.md``) and its proposals (11.8).

Runs read the memo as untrusted reference material (when the agent switched it on);
under ``agent`` enforcement no run writes it. The agent's editors change it
(``PUT /api/agents/{id}/memo``) and decide proposals; anyone who may run the agent can
propose: a ``memory_proposals`` row with ``scope='agent_memo'`` whose text editors see.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import AGENT_MEMO_PROPOSAL_DECIDE, AGENT_MEMO_UPDATE
from app.authz.memory_agent_access import pending_memo_proposals, shared_ids
from app.authz.org_context import OrgContext
from app.exceptions import ConflictError, ValidationError
from app.models.agent import Agent
from app.models.memory import AgentMemorySettings, MemoryProposal
from app.schemas.agent_memo import MAX_AGENT_MEMO_LENGTH
from app.services import audit_service, memory_service
from app.services.agent_memo_files import commit_then_write, memo_path, read_memo
from app.services.memory_service import AGENT_MEMO_SCOPE

__all__ = ["memo_path", "read_memo"]
MAX_PENDING_PER_USER = 20


def _too_large() -> ValidationError:
    return ValidationError("AGENT_MEMO_TOO_LARGE", "운영 메모가 최대 길이를 넘습니다")


def _disabled() -> ConflictError:
    return ConflictError("AGENT_MEMO_DISABLED", "이 에이전트의 운영 메모가 꺼져 있습니다")


async def memo_enabled(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(AgentMemorySettings.shared_memo_enabled).where(
                AgentMemorySettings.agent_id == agent_id
            )
        )
    )


async def _set_enabled(db: AsyncSession, agent_id: uuid.UUID, enabled: bool) -> None:
    row = await db.get(AgentMemorySettings, agent_id)
    if row is None:
        db.add(AgentMemorySettings(agent_id=agent_id, shared_memo_enabled=enabled))
    else:
        row.shared_memo_enabled = enabled


async def _audit(
    db: AsyncSession, actor: OrgContext, agent: Agent, action: str, metadata: dict
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.user_id,
        owner_user_id=agent.user_id,
        action=action,
        target_type="agent",
        target_id=agent.id,
        target_name_snapshot=agent.name,
        target_owner_user_id=agent.user_id,
        outcome="success",
        metadata=metadata,  # ids, sizes and the switch only, never memo text
    )


async def update_memo(
    db: AsyncSession,
    *,
    actor: OrgContext,
    agent: Agent,
    content: str | None,
    enabled: bool | None = None,
) -> tuple[str, bool]:
    if content is not None:
        memory_service.validate_memory_text(content)
    if enabled is not None:
        await _set_enabled(db, agent.id, enabled)
    metadata = {"length": len(content) if content is not None else None, "enabled": enabled}
    await _audit(db, actor, agent, AGENT_MEMO_UPDATE, metadata)
    if content is None:
        await db.commit()
        text = read_memo(agent.id)
    else:
        text = await commit_then_write(agent.id, lambda _current: content, db.commit)
    return text, await memo_enabled(db, agent.id)


async def create_memo_proposal(
    db: AsyncSession,
    *,
    context: OrgContext,
    agent: Agent,
    content: str,
    reason: str | None,
    conversation_id: uuid.UUID | None,
) -> MemoryProposal:
    memory_service.validate_memory_text(content)
    memory_service.validate_memory_text(reason)
    if not await memo_enabled(db, agent.id):
        raise _disabled()
    await memory_service.validate_source_conversation(
        db, conversation_id=conversation_id, context=context, shared=shared_ids(agent, context)
    )
    pending = await pending_memo_proposals(db, context, agent.id)
    if pending >= MAX_PENDING_PER_USER:
        raise ConflictError("AGENT_MEMO_PROPOSAL_LIMIT", "검토 대기 중인 제안이 너무 많습니다")
    proposal = MemoryProposal(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=agent.id,
        conversation_id=conversation_id,
        scope=AGENT_MEMO_SCOPE,
        content=content,
        reason=reason,
    )
    db.add(proposal)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("AGENT_MEMO_PROPOSAL_REJECTED", "제안을 저장할 수 없습니다") from exc
    await db.refresh(proposal)
    return proposal


def _memo_proposals(agent: Agent) -> Select[tuple[MemoryProposal]]:
    return select(MemoryProposal).where(
        MemoryProposal.agent_id == agent.id,
        MemoryProposal.org_id == agent.org_id,
        MemoryProposal.tenant_id == agent.tenant_id,
        MemoryProposal.scope == AGENT_MEMO_SCOPE,
    )


async def list_memo_proposals(
    db: AsyncSession, agent: Agent, *, status: str = "pending"
) -> list[MemoryProposal]:
    result = await db.execute(
        _memo_proposals(agent)
        .where(MemoryProposal.status == status)
        .order_by(MemoryProposal.created_at.desc(), MemoryProposal.id)
    )
    return list(result.scalars().all())


def _appended(current: str, entry: str) -> str:
    prefix = current if not current or current.endswith("\n") else f"{current}\n"
    text = f"{prefix}- {entry}\n"
    if len(text) > MAX_AGENT_MEMO_LENGTH:
        raise _too_large()
    return text


async def decide_memo_proposal(
    db: AsyncSession,
    *,
    actor: OrgContext,
    agent: Agent,
    proposal_id: uuid.UUID,
    approve: bool,
    content: str | None = None,
) -> MemoryProposal | None:
    """Idempotent: the pending row is locked, so a second decision finds nothing."""
    proposal = await db.scalar(
        _memo_proposals(agent)
        .where(MemoryProposal.id == proposal_id, MemoryProposal.status == "pending")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if proposal is None:
        return None
    final = content if content is not None else proposal.content
    if approve:
        memory_service.validate_memory_text(final)
    proposal.content = final
    proposal.status = "approved" if approve else "rejected"
    proposal.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    decision = {"proposal_id": str(proposal.id), "decision": proposal.status}
    await _audit(db, actor, agent, AGENT_MEMO_PROPOSAL_DECIDE, decision)
    if approve:
        try:
            await commit_then_write(agent.id, lambda now: _appended(now, final), db.commit)
        except ValidationError:  # over the size cap: nothing was committed or written
            await db.rollback()
            raise
    else:
        await db.commit()
    await db.refresh(proposal)
    return proposal
