from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Response, status
from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.message_utils import content_to_text
from app.dependencies import get_db, owned_conversation, verify_csrf
from app.models.conversation import Conversation
from app.schemas.conversation_pinned_summary import (
    PinConversationSummaryRequest,
    PinnedConversationSummaryEnvelope,
    PinnedConversationSummaryResponse,
)
from app.services.conversation_pinned_summary_service import (
    InvalidPinnedSummarySourceError,
    MessageRole,
    SummarySource,
    get_summary,
    pin_summary,
    serialize_summary,
    unpin_summary,
)
from app.services.thread_branch_service import (
    _build_tree_from_checkpoints,
    _collect_checkpoints,
)

router = APIRouter(tags=["conversations"])
_TURN_ID_SEPARATOR = "::moldy-turn-"


def _source_message_id(message_id: str) -> str:
    base, separator, suffix = message_id.rpartition(_TURN_ID_SEPARATOR)
    return base if separator and suffix.isdigit() else message_id


def _message_role(message: BaseMessage) -> MessageRole | None:
    match getattr(message, "type", None):
        case "ai":
            return "assistant"
        case "human":
            return "user"
        case "tool":
            return "tool"
        case "system":
            return "system"
        case _:
            return None


def _sources_for_messages(
    messages: Sequence[BaseMessage],
    branch_checkpoint_id: str,
) -> tuple[SummarySource, ...]:
    """Reproduce assistant-ui's stable cross-turn message identity contract."""

    sources: list[SummarySource] = []
    index_by_scoped_id: dict[tuple[int, str], int] = {}
    first_turn_by_source_id: dict[str, int] = {}
    turn_index = -1
    for message_index, message in enumerate(messages):
        role = _message_role(message)
        if role == "user":
            turn_index += 1
        if role is None:
            continue
        raw_id = str(getattr(message, "id", None) or f"synthetic-{message_index}")
        source_id = _source_message_id(raw_id)
        first_turn = first_turn_by_source_id.get(source_id)
        first_turn_by_source_id.setdefault(source_id, turn_index)
        message_id = (
            f"{source_id}{_TURN_ID_SEPARATOR}{turn_index}"
            if first_turn is not None and first_turn != turn_index
            else raw_id
        )
        source = SummarySource(
            message_id=message_id,
            role=role,
            text=content_to_text(message.content),
            branch_checkpoint_id=branch_checkpoint_id,
        )
        scoped_id = (turn_index, source_id)
        existing_index = index_by_scoped_id.get(scoped_id)
        if existing_index is None:
            index_by_scoped_id[scoped_id] = len(sources)
            sources.append(source)
        else:
            existing_id = sources[existing_index].message_id
            sources[existing_index] = SummarySource(
                message_id=existing_id,
                role=source.role,
                text=source.text,
                branch_checkpoint_id=source.branch_checkpoint_id,
            )
    return tuple(sources)


async def _load_sources(
    conversation_id: uuid.UUID,
    active_checkpoint_id: str | None,
) -> tuple[tuple[SummarySource, ...], tuple[SummarySource, ...]]:
    checkpoints = await _collect_checkpoints(get_checkpointer(), str(conversation_id))
    if not checkpoints:
        return (), ()
    tree = _build_tree_from_checkpoints(checkpoints, active_checkpoint_id)
    active_sources = (
        _sources_for_messages(
            [node.message for node in tree.nodes],
            tree.active_checkpoint_id or tree.nodes[-1].introduced_by_checkpoint_id,
        )
        if tree.nodes
        else ()
    )
    historical_sources: list[SummarySource] = []
    for checkpoint in checkpoints:
        historical_sources.extend(
            _sources_for_messages(checkpoint.messages, checkpoint.checkpoint_id)
        )
    return active_sources, tuple(historical_sources)


@router.get(
    "/api/conversations/{conversation_id}/pinned-summary",
    response_model=PinnedConversationSummaryEnvelope,
)
async def read_pinned_summary(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    conv: Conversation = Depends(owned_conversation),
) -> PinnedConversationSummaryEnvelope:
    row = await get_summary(db, conversation_id)
    if row is None:
        return PinnedConversationSummaryEnvelope(summary=None)
    active, historical = await _load_sources(conversation_id, conv.active_branch_checkpoint_id)
    return PinnedConversationSummaryEnvelope(summary=serialize_summary(row, active, historical))


@router.put(
    "/api/conversations/{conversation_id}/pinned-summary",
    response_model=PinnedConversationSummaryResponse,
)
async def write_pinned_summary(
    conversation_id: uuid.UUID,
    data: PinConversationSummaryRequest,
    db: AsyncSession = Depends(get_db),
    conv: Conversation = Depends(owned_conversation),
    _csrf: None = Depends(verify_csrf),
) -> PinnedConversationSummaryResponse:
    active, historical = await _load_sources(conversation_id, conv.active_branch_checkpoint_id)
    try:
        row = await pin_summary(db, conversation_id, data.message_id, active)
    except InvalidPinnedSummarySourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_pinned_summary_source", "reason": exc.reason},
        ) from exc
    await db.commit()
    return serialize_summary(row, active, historical)


@router.delete(
    "/api/conversations/{conversation_id}/pinned-summary",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_pinned_summary(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _conv: Conversation = Depends(owned_conversation),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    await unpin_summary(db, conversation_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
