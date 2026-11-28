"""Pin a rolled-over directive to the active continuation of its original run."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.chat_steer_checkpoint import run_checkpoint_for_steers
from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.authz.conversation_steer_scope import steer_run_actor_scope
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

logger = logging.getLogger(__name__)


async def _history(
    saver: BaseCheckpointSaver[Any], root: RunnableConfig
) -> AsyncIterator[CheckpointTuple]:
    # The Postgres saver materializes each alist query. Release its cursor after
    # a bounded page instead of loading the entire conversation into memory.
    before = None
    page_size = 1  # The source run is usually still the head.
    while True:
        page = [item async for item in saver.alist(root, before=before, limit=page_size)]
        for item in page:
            yield item
        if len(page) < page_size:
            return
        before = page[-1].config
        page_size = 32


async def _continuation(
    saver: BaseCheckpointSaver[Any], conversation: Conversation, required: set[str], tenant_id: UUID
) -> str | None:
    """Resolve the UI's newest descendant of its selection; prove every required ancestor."""
    thread_id = str(conversation.id)
    root: RunnableConfig = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    selected = conversation.active_branch_checkpoint_id
    if selected is not None:
        required.add(selected)

    # alist is newest first. Propagate the newest descendant's rank to each
    # parent, keeping just ids. No message or tool history is replayed.
    parents: dict[str, str | None] = {}
    newest: dict[str, tuple[int, str]] = {}
    head = None
    async for saved in _history(saver, root):
        cfg = saved.config.get("configurable", {})
        checkpoint = saved.checkpoint["id"]
        if (
            cfg.get("thread_id") != thread_id
            or cfg.get("checkpoint_ns") != ""
            or cfg.get("checkpoint_id") != checkpoint
            or checkpoint in parents
        ):
            return None
        if head is None:
            head = checkpoint
        if saved.metadata.get(CHECKPOINT_TENANT_KEY) != str(tenant_id):
            parents[checkpoint] = None
            newest.pop(checkpoint, None)
            if checkpoint in required or (selected is None and checkpoint == head):
                return None
            continue
        parent_cfg = (saved.parent_config or {}).get("configurable", {})
        parent = parent_cfg.get("checkpoint_id")
        if parent and (
            parent_cfg.get("thread_id") != thread_id or parent_cfg.get("checkpoint_ns") != ""
        ):
            return None
        rank, tip = newest.setdefault(checkpoint, (len(parents), checkpoint))
        parents[checkpoint] = parent
        if parent:
            newest[parent] = min(newest.get(parent, (rank, tip)), (rank, tip))
        if required.issubset(parents):
            break

    anchor = selected or head
    if anchor is None or anchor not in newest or not required.issubset(parents):
        return None
    tip = newest[anchor][1]
    cursor: str | None = tip
    while cursor is not None:
        required.discard(cursor)
        if not required:
            return tip
        # Removing visited entries also makes a corrupt cycle fail closed.
        cursor = parents.pop(cursor, None)
    return None


async def _pin(
    db: AsyncSession,
    conversation: Conversation,
    queued: ConversationRunInput,
    steer: ConversationRunSteer,
    context: OrgContext,
) -> bool:
    saver = await conversation_checkpointer(str(conversation.id), context, db=db)
    source = await run_checkpoint_for_steers(
        conversation.id, steer.run_id, saver=saver, tenant_id=context.tenant_id
    )
    anchors = {steer.source_branch_checkpoint_id} if steer.source_branch_checkpoint_id else set()
    if source is not None:
        anchors.add(source.checkpoint["id"])
    else:
        # A queued run can terminate before any worker ever starts it. Its
        # admission branch, or a verified still-empty thread, is sufficient.
        never_started = await db.scalar(
            select(ConversationRun.id).where(
                ConversationRun.id == steer.run_id,
                ConversationRun.conversation_id == conversation.id,
                steer_run_actor_scope(context),
                ConversationRun.agent_id == queued.agent_id,
                ConversationRun.is_active.is_(False),
                ConversationRun.status.in_(("canceled", "failed", "stale")),
                ConversationRun.started_at.is_(None),
                ConversationRun.worker_instance_id.is_(None),
            )
        )
        if never_started is None:
            return False
        if not anchors:
            root: RunnableConfig = {
                "configurable": {"thread_id": str(conversation.id), "checkpoint_ns": ""}
            }
            return (
                conversation.active_branch_checkpoint_id is None
                and await saver.aget_tuple(root) is None
            )
    queued.checkpoint_id = await _continuation(saver, conversation, anchors, context.tenant_id)
    return queued.checkpoint_id is not None


async def pin_steer_continuation(
    db: AsyncSession,
    conversation: Conversation,
    queued: ConversationRunInput,
    context: OrgContext,
) -> bool:
    """Verify only server-generated rollover inputs, under the conversation and input locks."""
    if queued.source != "steer":
        return True
    steer = await db.scalar(
        select(ConversationRunSteer)
        .where(
            ConversationRunSteer.queued_input_id == queued.id,
            ConversationRunSteer.conversation_id == conversation.id,
            private_scope_predicate(ConversationRunSteer, context),
            ConversationRunSteer.status == "queued",
        )
        .with_for_update()
    )
    if steer is not None:
        try:
            if await _pin(db, conversation, queued, steer, context):
                return True
        except SQLAlchemyError:
            raise  # Let the worker retry the entire transaction.
        except Exception as exc:  # noqa: BLE001 - an unproven branch cannot start a new run
            logger.warning("Steer branch unavailable for %s (%s)", steer.id, type(exc).__name__)
        steer.status = "needs_review"
        steer.queued_input_id = None
    queued.status = "canceled"
    queued.revision += 1
    await db.flush()
    return False
