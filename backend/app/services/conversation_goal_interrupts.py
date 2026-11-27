"""Classify a goal's own paused input without copying prompts or tool arguments into its state."""

from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer, deferred_saver
from app.agent_runtime.langgraph_pending_inputs import interrupt_payloads_from_checkpointer
from app.approvals.recorded_interrupts import recorded_approval_displays
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run import ConversationRun

InputReason = Literal["input_required", "approval_pending"]


def known_input_reason(payload: Mapping[str, object]) -> InputReason | None:
    if payload.get("type") in ("auth_required", "ask_user"):
        return "input_required"
    actions = payload.get("action_requests")
    if not isinstance(actions, list) or not actions:
        return None
    names = [item.get("name") for item in actions if isinstance(item, Mapping)]
    if len(names) != len(actions) or any(not isinstance(name, str) or not name for name in names):
        return None
    if all(name == "ask_user" for name in names):
        return "input_required"
    return "approval_pending" if isinstance(payload.get("review_configs"), list) else None


async def goal_interrupt_reason(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: str | None,
    context: OrgContext,
) -> InputReason:
    # An unidentified interrupt must never create an actionable approval. The
    # approval publisher requires an actual current ApprovalRequest or checkpoint.
    if not interrupt_id:
        return "approval_pending"
    run = await db.scalar(
        select(ConversationRun).where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.status == "interrupted",
            ConversationRun.interrupt_id == interrupt_id,
            private_scope_predicate(ConversationRun, context),
        )
    )
    if run is None:
        return "approval_pending"
    recorded = (await recorded_approval_displays(db, run)).get(interrupt_id)
    if recorded is not None and (reason := known_input_reason(recorded)):
        return reason

    thread_id = str(conversation_id)
    saver = await conversation_checkpointer(thread_id, context, db=db, saver=deferred_saver)
    inputs = await interrupt_payloads_from_checkpointer(
        {"configurable": {"thread_id": thread_id}}, saver
    )
    for item in inputs:
        if item["interrupt_id"] == interrupt_id:
            return known_input_reason(item["payload"]) or "approval_pending"
    return "approval_pending"


async def waiting_goal_input_reason(
    db: AsyncSession, *, conversation_id: UUID, context: OrgContext
) -> InputReason | None:
    from app.services.conversation_run_service import get_latest_interrupted_run

    waiting = await get_latest_interrupted_run(db, conversation_id=conversation_id, context=context)
    if waiting is None:
        return None
    return await goal_interrupt_reason(
        db,
        conversation_id=conversation_id,
        run_id=waiting.id,
        interrupt_id=waiting.interrupt_id,
        context=context,
    )
