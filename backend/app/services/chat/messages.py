"""Checkpointer-backed message listing.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change. ``list_messages_from_checkpointer`` is the single read-path entry
that rebuilds the active branch's messages with timestamps, branch info,
HITL hydration, redaction, and side-channel metadata.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import langchain_messages_to_response, parse_msg_id
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.message_feedback import MessageFeedback
from app.schemas.conversation import MessageAttachmentBrief, MessageFeedbackBrief
from app.services.artifact_service import list_conversation_artifacts_by_message_id
from app.services.chat.interrupts import _hydrate_pending_interrupt_tool_calls
from app.services.chat.secrets import (
    _redact_response_tool_calls,
    collect_conversation_secret_values,
)
from app.services.chat.usage import _resolve_agent_model_pricing

logger = logging.getLogger(__name__)


async def list_messages_from_checkpointer(
    db: AsyncSession,
    conversation: Conversation,
    user_id: uuid.UUID | None = None,
    *,
    tree: Any = None,
) -> list:
    """Return persisted messages, attaching stable per-message timestamps.

    LangChain ``BaseMessage`` carries no timestamp metadata, so we keep an
    ``idx → ISO`` mapping in ``Conversation.message_timestamps``. The first
    time a message is exposed we stamp it with the current time; subsequent
    reads reuse the stored value so old messages don't drift on every fetch.

    M-CHAT1b: when the conversation has multiple branches we now walk the
    full checkpoint tree (not just the latest checkpoint) so each
    ``MessageResponse`` carries ``parent_id`` / ``branch_checkpoint_id`` /
    ``siblings`` for assistant-ui's BranchPicker. The legacy callers (and
    legacy tests) that expect a flat list are unaffected — for a thread with
    no branching this returns the same active linear list as before.

    When ``user_id`` is provided, each ``MessageResponse`` is hydrated with
    the caller's existing feedback rating (P0-1c) and any attachments linked
    by message id (P1-7).
    """

    # P0-D: tree를 호출자가 미리 만들어 넘기면 build_message_tree 중복 호출
    # (= _collect_checkpoints + alist 전체 walk)을 피한다. 단독으로 부르면
    # 하위호환 유지를 위해 직접 build.
    if tree is None:
        from app.agent_runtime.checkpointer import get_checkpointer
        from app.services.thread_branch_service import build_message_tree

        checkpointer = get_checkpointer()
        tree = await build_message_tree(
            checkpointer,
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )

    if not tree.nodes:
        return []

    messages = [node.message for node in tree.nodes]

    stored_timestamps: dict[str, str] = dict(conversation.message_timestamps or {})
    timestamps: list[datetime] = []
    fallback_base = conversation.created_at

    for idx, msg in enumerate(messages):
        msg_uuid = parse_msg_id(getattr(msg, "id", None), conversation.id, idx)
        key = str(msg_uuid)
        iso = stored_timestamps.get(key)
        ts = datetime.fromisoformat(iso) if iso else fallback_base + timedelta(milliseconds=idx)
        timestamps.append(ts)

    # W7-4 — conversation의 agent에 연결된 model 단가를 한 번 조회해 넘긴다.
    # 메시지마다 model이 다를 수 있으나(fallback chain) 단순화 — 95% 케이스인
    # default model 단가만 사용해 근사. 정확한 누적은 Daily Spend가 별도로 추적.
    cost_per_input, cost_per_output = await _resolve_agent_model_pricing(db, conversation)

    responses = langchain_messages_to_response(
        messages,
        conversation.id,
        timestamps=timestamps,
        cost_per_input_token=cost_per_input,
        cost_per_output_token=cost_per_output,
    )

    # Attach branch tree info — parent_id, siblings, branch_checkpoint_id.
    # We pre-compute msg id → response idx so parent/sibling lookups are O(1).
    raw_to_uuid: dict[str, uuid.UUID] = {}
    for idx, msg in enumerate(messages):
        raw = str(getattr(msg, "id", None) or f"synthetic-{idx}")
        raw_to_uuid[raw] = parse_msg_id(getattr(msg, "id", None), conversation.id, idx)

    # Pre-compute uuids for *every* sibling raw id we may reference (siblings
    # for the active node may live on non-active leaves whose raw ids don't
    # appear in ``raw_to_uuid`` yet — derive them with the same parse_msg_id
    # logic so the frontend ids are consistent).
    def _sibling_uuid(raw: str, idx: int) -> uuid.UUID:
        if raw in raw_to_uuid:
            return raw_to_uuid[raw]
        # Synthesize using the same fallback rule as the active chain.
        synth = None if raw.startswith("synthetic-") else raw
        return parse_msg_id(synth, conversation.id, idx)

    for idx, (resp, node) in enumerate(zip(responses, tree.nodes, strict=False)):
        resp.branch_checkpoint_id = node.introduced_by_checkpoint_id
        if node.parent_id:
            resp.parent_id = raw_to_uuid.get(node.parent_id)
        # Sibling map keyed by the raw langchain id.
        raw_id = str(getattr(node.message, "id", None) or f"synthetic-{idx}")
        sibling_entries = tree.branches_by_message.get(raw_id, [])
        resp.siblings = [_sibling_uuid(s.message_id, idx) for s in sibling_entries]
        resp.sibling_checkpoint_ids = [s.checkpoint_id for s in sibling_entries]
        resp.branch_index = node.branch_index
        resp.branch_total = node.branch_total

    if user_id is not None:
        await _hydrate_pending_interrupt_tool_calls(
            db,
            conversation_id=conversation.id,
            responses=responses,
        )
    secrets = tuple(await collect_conversation_secret_values(db, conversation))
    _redact_response_tool_calls(responses, secret_values=secrets)

    # Hydrate per-message feedback (current user) + attachments/artifacts. Wrapped in
    # broad try/except so a missing migration (m27/m28 not yet applied) or
    # any other query glitch degrades gracefully — the message list still
    # renders, just without the side-channel metadata.
    feedback_by_msg: dict[str, str] = {}
    attachments_by_msg: dict[str, list[MessageAttachmentBrief]] = {}
    artifacts_by_msg: dict[str, list[Any]] = {}

    if user_id is not None:
        try:
            result = await db.execute(
                select(MessageFeedback).where(
                    MessageFeedback.user_id == user_id,
                    MessageFeedback.conversation_id == conversation.id,
                )
            )
            for fb in result.scalars().all():
                feedback_by_msg[fb.message_id] = fb.rating
        except Exception:  # noqa: BLE001 — non-critical hydration
            logger.warning(
                "feedback hydrate failed for conversation %s — skipping",
                conversation.id,
                exc_info=True,
            )

    # D11/M2 — attachments are an authed-view side channel only. A public
    # share reads with ``user_id=None``; gating here keeps attachments (now
    # that message_id is backfilled) out of the share snapshot, matching the
    # artifact block below which is already authed-only.
    if user_id is not None:
        try:
            attach_result = await db.execute(
                select(MessageAttachment).where(
                    MessageAttachment.conversation_id == conversation.id,
                    MessageAttachment.message_id.is_not(None),
                )
            )
            for att in attach_result.scalars().all():
                if att.message_id is None:
                    continue
                attachments_by_msg.setdefault(att.message_id, []).append(
                    MessageAttachmentBrief.model_validate(att)
                )
        except Exception:  # noqa: BLE001 — non-critical hydration
            logger.warning(
                "attachment hydrate failed for conversation %s — skipping",
                conversation.id,
                exc_info=True,
            )

    if user_id is not None:
        try:
            artifacts_by_msg = await list_conversation_artifacts_by_message_id(
                db,
                user_id=user_id,
                conversation_id=conversation.id,
            )
        except Exception:  # noqa: BLE001 — non-critical hydration
            logger.warning(
                "artifact hydrate failed for conversation %s — skipping",
                conversation.id,
                exc_info=True,
            )

    for resp in responses:
        mid = str(resp.id)
        rating = feedback_by_msg.get(mid)
        if rating:
            resp.feedback = MessageFeedbackBrief(rating=rating)
        atts = attachments_by_msg.get(mid)
        if atts:
            resp.attachments = atts
        artifacts = artifacts_by_msg.get(mid)
        if artifacts:
            resp.artifacts = artifacts

    return responses
