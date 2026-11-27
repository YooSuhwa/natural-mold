"""Reconcile durable, same-run instructions after their owning run terminates."""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.conversation_steer_scope import steer_run_parent_scope
from app.authz.org_context import OrgContext
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.scoping import private_scope_predicate
from app.database import TenantScoped
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun, utc_now_naive
from app.models.conversation_run_steer import ConversationRunSteer
from app.services.conversation_run_queue_persistence import enqueue_input_with_result
from app.services.conversation_run_steers import lock_steer_conversation

logger = logging.getLogger(__name__)


def _pending_terminal_targets() -> Select[tuple[UUID, UUID, UUID, UUID, UUID]]:
    """A recovery scan needs only identities, including for an owner who lost membership."""
    return (
        select(
            ConversationRun.id,
            Conversation.id.label("conversation_id"),
            Conversation.user_id,
            Conversation.org_id,
            Conversation.tenant_id,
        )
        .select_from(ConversationRunSteer)
        .join(ConversationRun, ConversationRun.id == ConversationRunSteer.run_id)
        .join(Conversation, Conversation.id == ConversationRun.conversation_id)
        .where(
            ConversationRunSteer.status == "pending",
            steer_run_parent_scope(),
            ConversationRun.status.in_(RUN_TERMINAL_STATUSES),
            ConversationRun.is_active.is_(False),
        )
        .distinct()
    )


async def _queue_at_end(db: AsyncSession, steer: ConversationRunSteer, context: OrgContext) -> None:
    # The receipt and input commit together under the conversation lock. An unrelated
    # caller-supplied key that happens to match must never be adopted as our input.
    try:
        result = await enqueue_input_with_result(
            db,
            conversation_id=steer.conversation_id,
            context=context,
            client_request_id=f"steer:{steer.id}",
            source="steer",
            input_payload={"messages": [{"role": "user", "content": steer.text}]},
            checkpoint_id=None,
            attachment_ids=[],
            priority=0,
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        steer.status = "needs_review"
        return
    if result.created:
        steer.queued_input_id = result.input.id
        steer.status = "queued"
    else:
        steer.status = "needs_review"


async def _confirmed_deliveries(
    db: AsyncSession,
    context: OrgContext,
    run: ConversationRun,
    rows: list[ConversationRunSteer],
) -> frozenset[UUID]:
    attempted = [row.id for row in rows if row.delivery_started_at is not None]
    if not attempted:
        return frozenset()
    try:
        saver = await conversation_checkpointer(str(run.conversation_id), context, db=db)
    except RuntimeError:
        logger.warning("checkpoint saver unavailable for terminal instructions run_id=%s", run.id)
        return frozenset()
    return await confirmed_steer_ids(
        run.conversation_id, run.id, attempted, saver=saver, tenant_id=context.tenant_id
    )


async def reconcile_terminal_steers(
    db: AsyncSession, *, conversation_id: UUID, run_id: UUID, context: OrgContext
) -> None:
    """Finish pending receipts in one transaction; the caller commits or retries.

    A committed claim precedes any proposed change to graph state. Never move a
    claimed instruction into a new run: a lost worker may have delivered it.
    Claims and admissions use the same parent-first lock order as this rollover.
    """
    # Detached workers may inherit their originating request's FGA decision memo.
    with fresh_run_decision_scope():
        await _reconcile(db, conversation_id, run_id, context)


async def _reconcile(
    db: AsyncSession, conversation_id: UUID, run_id: UUID, context: OrgContext
) -> None:
    target = _pending_terminal_targets().where(
        Conversation.id == conversation_id,
        ConversationRun.id == run_id,
        private_scope_predicate(Conversation, context),
    )
    if await db.scalar(target) is None:
        return
    pending = (
        ConversationRunSteer.conversation_id == conversation_id,
        ConversationRunSteer.run_id == run_id,
        private_scope_predicate(ConversationRunSteer, context),
        ConversationRunSteer.status == "pending",
    )
    try:
        agent_id = await lock_steer_conversation(db, conversation_id, context)
    except (AppError, HTTPException) as exc:
        status = exc.status if isinstance(exc, AppError) else exc.status_code
        if status >= 500:
            raise
        # Settlement-only after revocation: no instruction text, checkpoint, or queue access.
        await db.execute(update(ConversationRunSteer).where(*pending).values(status="needs_review"))
        return
    run = await lock_executable_run(
        db, RunExecutionTarget(run_id, RunConversationTarget(conversation_id, agent_id, context))
    )
    if (
        run is None
        or run.org_id not in (None, context.org_id)
        or run.tenant_id not in (None, context.tenant_id)
    ):
        # The conversation may now be bound to another agent. Its successor must
        # never inherit instructions authorized against the original agent.
        await db.execute(update(ConversationRunSteer).where(*pending).values(status="needs_review"))
        return
    if run.is_active or run.status not in RUN_TERMINAL_STATUSES:
        return
    rows = list(
        await db.scalars(
            select(ConversationRunSteer)
            .where(*pending)
            .order_by(ConversationRunSteer.sequence)
            .with_for_update()
        )
    )
    confirmed = await _confirmed_deliveries(db, context, run, rows)
    for row in rows:
        if row.id in confirmed:
            row.status = "applied"
            row.applied_at = utc_now_naive()
        elif row.delivery_started_at is not None:
            row.status = "needs_review"
        else:
            await _queue_at_end(db, row, context)

    if any(row.status == "queued" for row in rows) and (
        run.status == "stale"
        or run.error_code == "prepared_run_lease_missing"
        or any(row.status == "needs_review" for row in rows)
    ):
        # The FIFO may outlive its previous worker. Preserve user control when a
        # preceding instruction or the previous worker's effects are uncertain.
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.queue_paused = True
            conversation.queue_paused_at = conversation.queue_paused_at or utc_now_naive()
    await db.flush()


async def settle_terminal_steers(
    company_session: Callable[[TenantScoped], AsyncSession],
    *,
    conversation_id: UUID,
    run_id: UUID,
    context: OrgContext,
) -> None:
    try:
        async with company_session(context) as db:
            await reconcile_terminal_steers(
                db, conversation_id=conversation_id, run_id=run_id, context=context
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - the persisted pending row will be retried by queue recovery
        logger.exception("terminal instruction reconciliation deferred run_id=%s", run_id)


async def recover_terminal_steers(
    queue_scan_session: Callable[[], AsyncSession],
    company_session: Callable[[TenantScoped], AsyncSession],
) -> None:
    """After restart, recover even receipts admitted before the feature was disabled."""
    async with queue_scan_session() as db:
        candidates = (await db.execute(_pending_terminal_targets())).all()
    for run_id, conversation_id, user_id, org_id, tenant_id in candidates:
        await settle_terminal_steers(
            company_session,
            conversation_id=conversation_id,
            run_id=run_id,
            context=OrgContext(user_id=user_id, org_id=org_id, tenant_id=tenant_id),
        )
