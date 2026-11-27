"""Durable user instructions for the next model boundary of a running chat.

Writers flush into the caller's transaction. The conversation lock serializes
request IDs and sequences with terminal reconciliation and goal transitions.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.memory_paths import origin_run
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.conversation_scope import load_private_conversation
from app.authz.conversation_steer_scope import (
    steer_queue_input_scope,
    steer_queue_successor_scope,
    steer_run_actor_scope,
)
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import lock_query
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.scoping import private_scope_predicate
from app.models.agent import Agent
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer
from app.services import conversation_run_service
from app.services.conversation_run_queue_mutations import (
    lock_owned_conversation,
    require_queue_executable,
)


@dataclass(frozen=True, slots=True)
class SteerWatermarks:
    latest_received: int
    latest_applied: int
    has_unresolved: bool


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Conversation run not found")


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code})


async def lock_steer_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> UUID:
    # Check the relation before the canonical execution gate locks the agent and conversation.
    candidate = await db.scalar(
        select(Conversation.agent_id).where(
            Conversation.id == conversation_id, private_scope_predicate(Conversation, context)
        )
    )
    if candidate is None:
        raise _not_found()
    await require_agent_run(context, candidate)
    agent_id = await require_queue_executable(db, conversation_id, context)
    if agent_id != candidate:
        raise _not_found()
    conversation = await lock_owned_conversation(
        db, conversation_id=conversation_id, context=context
    )
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        raise _conflict("STEER_RUN_INACTIVE")
    return agent_id


async def _lock_target_run(
    db: AsyncSession, conversation_id: UUID, run_id: UUID, agent_id: UUID, context: OrgContext
) -> ConversationRun | None:
    # Caller already holds the private parent. Unattributed legacy rows inherit the
    # parent's scope, but an explicit different owner, agent, or company cannot.
    return await lock_query(
        db,
        LockClass.RUN,
        select(ConversationRun)
        .where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.agent_id == agent_id,
            steer_run_actor_scope(context),
        )
        .with_for_update(of=ConversationRun)
        .execution_options(populate_existing=True),
    )


async def _require_steerable(db: AsyncSession, run: ConversationRun, context: OrgContext) -> None:
    interrupted = await conversation_run_service.get_latest_interrupted_run(
        db, conversation_id=run.conversation_id, context=context
    )
    waiting = await db.scalar(
        select(ApprovalRequest.id)
        .where(
            ApprovalRequest.conversation_id == run.conversation_id,
            ApprovalRequest.tenant_id == context.tenant_id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.kind == "tool",
        )
        .limit(1)
    )
    if interrupted or waiting:
        raise _conflict("STEER_APPROVAL_PENDING")
    if not run.is_active or run.status not in ("queued", "running") or run.cancel_requested_at:
        raise _conflict("STEER_RUN_INACTIVE")

    first = await origin_run(db, run)
    profile = await db.scalar(select(Agent.runtime_profile).where(Agent.id == run.agent_id))
    if (
        profile != "standard"
        or first is None
        or first.conversation_id != run.conversation_id
        or first.user_id != context.user_id
        or first.run_source not in (None, "CHAT")
        or first.source not in ("chat", "start", "edit", "regenerate")
    ):
        raise _conflict("STEER_RUN_INACTIVE")


async def admit_steer(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_id: UUID,
    context: OrgContext,
    client_request_id: UUID,
    text: str,
) -> ConversationRunSteer:
    """Return the original receipt on retry even after its run ends."""
    text = text.strip()
    if not 1 <= len(text) <= 12000 or "\x00" in text:
        raise HTTPException(status_code=422, detail="Steer text must contain 1 to 12000 characters")
    agent_id = await lock_steer_conversation(db, conversation_id, context)
    conversation = await lock_owned_conversation(
        db, conversation_id=conversation_id, context=context
    )
    run = await _lock_target_run(db, conversation_id, run_id, agent_id, context)
    if run is None:
        raise _not_found()

    existing = await db.scalar(
        select(ConversationRunSteer).where(
            ConversationRunSteer.conversation_id == conversation_id,
            ConversationRunSteer.client_request_id == client_request_id,
            private_scope_predicate(ConversationRunSteer, context),
        )
    )
    if existing is not None:
        if existing.run_id != run_id or existing.text != text:
            raise _conflict("STEER_IDEMPOTENCY_CONFLICT")
        return existing
    await _require_steerable(db, run, context)
    last_sequence = await db.scalar(
        select(func.max(ConversationRunSteer.sequence)).where(
            ConversationRunSteer.conversation_id == conversation_id
        )
    )
    row = ConversationRunSteer(
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        client_request_id=client_request_id,
        text=text,
        sequence=(last_sequence or 0) + 1,
        source_branch_checkpoint_id=conversation.active_branch_checkpoint_id,
    )
    db.add(row)
    await db.flush()
    return row


async def list_steers(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    context: OrgContext,
    run_id: UUID | None = None,
    before_sequence: int | None = None,
    limit: int = 50,
) -> list[ConversationRunSteer]:
    conversation = await load_private_conversation(db, conversation_id, context)
    if conversation is None:
        raise _not_found()
    readable = await conversation_run_service.readable_conversation_run_ids(
        db, conversation, context
    )
    stmt = select(ConversationRunSteer).where(
        ConversationRunSteer.conversation_id == conversation_id,
        private_scope_predicate(ConversationRunSteer, context),
    )
    if readable is not None:
        if run_id is not None and run_id not in readable:
            raise _not_found()
        stmt = stmt.where(ConversationRunSteer.run_id.in_(readable))
    if run_id is not None:
        visible = await db.scalar(
            select(ConversationRun.id).where(
                ConversationRun.id == run_id,
                ConversationRun.conversation_id == conversation_id,
                steer_run_actor_scope(context),
            )
        )
        if visible is None:
            raise _not_found()
        stmt = stmt.where(ConversationRunSteer.run_id == run_id)
    if before_sequence is not None:
        stmt = stmt.where(ConversationRunSteer.sequence < before_sequence)
    rows = list(await db.scalars(stmt.order_by(ConversationRunSteer.sequence.desc()).limit(limit)))
    rows.reverse()
    return rows


async def claim_steers_for_model(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_id: UUID,
    checkpointed_ids: Collection[UUID] = (),
) -> list[ConversationRunSteer]:
    """Claim fresh rows. A previous attempt with no checkpoint proof blocks later rows."""
    where = (
        ConversationRunSteer.conversation_id == conversation_id,
        ConversationRunSteer.run_id == run_id,
        ConversationRunSteer.status.in_(("pending", "needs_review")),
    )
    actor = (
        await db.execute(
            select(
                ConversationRunSteer.user_id,
                ConversationRunSteer.org_id,
                ConversationRunSteer.tenant_id,
            )
            .where(*where)
            .limit(1)
        )
    ).one_or_none()
    if actor is None:
        return []
    context = await resolve_private_actor(db, PrivateResourceActor(*actor))
    # This detached worker may have inherited the request's memo while the user still
    # had can_run. Recheck the relation for this model boundary.
    try:
        with fresh_run_decision_scope():
            agent_id = await lock_steer_conversation(db, conversation_id, context)
        run = await _lock_target_run(db, conversation_id, run_id, agent_id, context)
        if run is None or run.status != "running" or not run.is_active:
            return []
        await _require_steerable(db, run, context)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return []
    rows = list(
        await db.scalars(
            select(ConversationRunSteer)
            .where(
                *where,
                private_scope_predicate(ConversationRunSteer, context),
            )
            .order_by(ConversationRunSteer.sequence)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    proven = set(checkpointed_ids)
    if any(
        row.status == "needs_review"
        or (row.delivery_started_at is not None and row.id not in proven)
        for row in rows
    ):
        return []
    fresh = [row for row in rows if row.delivery_started_at is None and row.id not in proven]
    now = utc_now_naive()
    for row in fresh:
        row.delivery_started_at = now
    await db.flush()
    return fresh


async def confirm_applied_steers(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_id: UUID,
    steer_ids: Collection[UUID],
) -> None:
    """Record only proven, previously claimed rows. A queued row cannot also be applied."""
    if not steer_ids:
        return
    scope = (
        await db.execute(
            select(Conversation.user_id, Conversation.org_id, Conversation.tenant_id)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )
    ).one_or_none()
    if scope is None:
        return
    context = OrgContext(user_id=scope.user_id, org_id=scope.org_id, tenant_id=scope.tenant_id)
    target = await db.scalar(
        select(ConversationRun.id)
        .where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            steer_run_actor_scope(context),
        )
        .with_for_update()
    )
    if target is None:
        return
    rows = await db.scalars(
        select(ConversationRunSteer)
        .where(
            ConversationRunSteer.conversation_id == conversation_id,
            ConversationRunSteer.run_id == run_id,
            private_scope_predicate(ConversationRunSteer, context),
            ConversationRunSteer.id.in_(steer_ids),
            ConversationRunSteer.delivery_started_at.is_not(None),
            ConversationRunSteer.status.in_(("pending", "needs_review")),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = utc_now_naive()
    for row in rows:
        row.applied_at = now
        row.status = "applied"
    await db.flush()


async def get_conversation_steer_watermarks(
    db: AsyncSession, conversation_id: UUID, *, after_sequence: int = 0
) -> SteerWatermarks:
    """Check the contiguous delivery prefix after the caller's accepted goal baseline."""
    receipt = ConversationRunSteer
    queued = ConversationRunInput
    run = ConversationRun
    latest = (
        await db.scalar(
            select(func.max(receipt.sequence)).where(receipt.conversation_id == conversation_id)
        )
        or 0
    )
    # Directly applied receipts already have a durable model checkpoint. Only pending
    # receipts and rollovers require inspection. Page the mutable queue payloads by
    # sequence so an old conversation never loads its entire instruction text history.
    outstanding = (
        select(
            receipt.sequence,
            receipt.status,
            receipt.text,
            receipt.user_id,
            receipt.org_id,
            receipt.tenant_id,
            queued.id.label("input_id"),
            queued.input_payload,
            queued.attachment_ids,
            run.id.label("successor_id"),
            run.status.label("successor_status"),
            run.agent_id,
            run.metadata_json,
        )
        .select_from(receipt)
        .outerjoin(
            queued,
            and_(
                steer_queue_input_scope(),
                queued.status == "claimed",
                queued.source == "chat",
            ),
        )
        .outerjoin(
            run,
            and_(
                steer_queue_successor_scope(),
                run.source == "chat",
            ),
        )
        .where(
            receipt.conversation_id == conversation_id,
            receipt.sequence <= latest,
            receipt.status != "applied",
        )
        .order_by(receipt.sequence)
    )
    cursor = after_sequence
    while cursor < latest:
        rows = (await db.execute(outstanding.where(receipt.sequence > cursor).limit(32))).all()
        if not rows:
            break
        for row in rows:
            # A queued input can be edited or canceled by its owner. The successor
            # must have claimed the exact message; a later unrelated success is no proof.
            bound = (
                row.status == "queued"
                and row.successor_id is not None
                and row.input_payload == {"messages": [{"role": "user", "content": row.text}]}
                and row.attachment_ids == []
                and isinstance(row.metadata_json, dict)
                and row.metadata_json.get("queue_input_id") == str(row.input_id)
            )
            complete = bound and row.successor_status == "completed"
            if bound and row.successor_status == "interrupted":
                complete = await _completed_rollover_resume(
                    db,
                    conversation_id,
                    OrgContext(user_id=row.user_id, org_id=row.org_id, tenant_id=row.tenant_id),
                    row.agent_id,
                    row.successor_id,
                )
            if not complete:
                return SteerWatermarks(latest, row.sequence - 1, True)
            cursor = row.sequence
    return SteerWatermarks(latest, latest, False)


async def _completed_rollover_resume(
    db: AsyncSession,
    conversation_id: UUID,
    context: OrgContext,
    agent_id: UUID,
    successor_id: UUID,
) -> bool:
    """The approved continuation must descend from this exact queued run."""
    run = ConversationRun
    chain = (
        select(run.id, run.parent_run_id, run.status, run.tenant_id, run.org_id)
        .where(run.id == successor_id)
        .cte("rollover_resumes", recursive=True)
    )
    chain = chain.union(
        select(run.id, run.parent_run_id, run.status, run.tenant_id, run.org_id)
        .join(chain, run.parent_run_id == chain.c.id)
        .where(
            chain.c.status == "interrupted",
            run.source == "resume",
            run.conversation_id == conversation_id,
            private_scope_predicate(run, context),
            run.agent_id == agent_id,
            run.tenant_id == chain.c.tenant_id,
            run.org_id == chain.c.org_id,
        )
    )
    # More than one resume for a predecessor is ambiguous, even if one completed.
    rows = (
        await db.execute(
            select(chain.c.parent_run_id, chain.c.status).where(chain.c.id != successor_id)
        )
    ).all()
    return len(rows) == len({row.parent_run_id for row in rows}) and any(
        row.status == "completed" for row in rows
    )
