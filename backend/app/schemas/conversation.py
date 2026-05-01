from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, PlainSerializer


def _utc_iso(dt: datetime) -> str:
    """timezone-naive datetime을 UTC ISO 문자열(Z suffix)로 직렬화.

    백엔드는 datetime을 `datetime.now(UTC).replace(tzinfo=None)`로 저장하므로
    값은 UTC지만 tzinfo가 비어 있다. Pydantic 기본 직렬화는 'Z' 없이 보내
    JS `new Date(s)`가 로컬 시간으로 해석하는 함정을 유발한다.
    """
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None


class ConversationResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    title: str | None
    is_pinned: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime

    model_config = {"from_attributes": True}


class ResumeRequest(BaseModel):
    response: str | list[str] | dict[str, Any]  # interrupt 응답값


class MessageAttachmentRef(BaseModel):
    """Frontend → backend reference: link an existing upload row to a message.

    Only the upload id is needed; the row already carries the URL/mime/size
    and is reused when echoed back via ``MessageResponse.attachments``.
    """

    id: uuid.UUID


class MessageAttachmentBrief(BaseModel):
    """Inline attachment metadata exposed alongside ``MessageResponse``.

    The frontend renders thumbnails / download cards from this. Only the
    fields needed for preview are surfaced — full row goes through
    ``/api/uploads/{id}``.
    """

    id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    url: str

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str
    # Optional list of upload ids that should be attached to this message.
    # The router links them by patching ``MessageAttachment.message_id`` after
    # the LangGraph stream stamps the assistant message.
    attachments: list[MessageAttachmentRef] | None = None


class MessageFeedbackBrief(BaseModel):
    """Current user's feedback on a message (None if unrated)."""

    rating: str  # 'up' | 'down'


class TokenUsageBreakdown(BaseModel):
    """W7 — 메시지별 4종 토큰 분해 (LangChain ``usage_metadata`` 평탄화).

    LangChain은 ``input_token_details``로 cache_creation/cache_read를 분리해
    전달한다. 클라이언트 hover 팝오버가 직접 참조하므로 평탄한 4 필드 + 비용
    형태로 직렬화. 모든 필드 0이면 응답에서 ``null`` 자리를 반환하고 클라이언트는
    렌더 자체를 건너뛴다.
    """

    prompt_tokens: int
    completion_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    estimated_cost: float | None = None


class MessageResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    created_at: UtcDatetime
    feedback: MessageFeedbackBrief | None = None
    attachments: list[MessageAttachmentBrief] | None = None
    # W7 — assistant 메시지에서만 채워진다. user/tool 메시지나 LangChain이
    # ``usage_metadata``를 emit하지 않은 chunk는 ``None``.
    usage: TokenUsageBreakdown | None = None
    # M-CHAT1b — parent message id in the branch tree. ``None`` for the root
    # (first message). Frontend uses this to build assistant-ui's
    # ``messageRepository`` so BranchPicker auto-detects siblings.
    parent_id: uuid.UUID | None = None
    # checkpoint id this message was first emitted from. Frontend sends this
    # back via ``/switch-branch`` when the user picks a sibling so the next
    # turn forks from the right point.
    branch_checkpoint_id: str | None = None
    # sibling message ids (1-based BranchPicker counts derive from this list).
    # Empty list when there are no siblings. Active message id is included.
    siblings: list[uuid.UUID] = []
    # Per-sibling checkpoint ids — same order as ``siblings``. Frontend posts
    # the chosen sibling's checkpoint_id to ``/switch-branch`` to flip the
    # active branch. Empty when ``siblings`` is empty.
    sibling_checkpoint_ids: list[str] = []
    # M-CHAT1b HOTFIX2 — explicit (0-based) position of *this* message in the
    # sibling list, so the frontend can render ``<branch_index+1 / branch_total>``
    # without indexOf'ing the active id (which mis-fired when sibling order
    # didn't match the picker's left-right semantics). ``None`` when this
    # message has no siblings.
    branch_index: int | None = None
    branch_total: int | None = None


class MessagesEnvelope(BaseModel):
    """Wrapped message-list response.

    The plain ``list[MessageResponse]`` shape was used pre-M-CHAT1b. We now
    wrap it with branch metadata so the frontend can build a tree view; the
    list field name stays ``messages`` to match assistant-ui's external store
    adapter expectations.
    """

    messages: list[MessageResponse]
    active_tip_message_id: uuid.UUID | None = None
    active_checkpoint_id: str | None = None


class EditMessageRequest(BaseModel):
    """Edit a previous user message and re-run from there.

    ``message_id`` identifies the user message being replaced. Backend rewinds
    to the checkpoint just before it and forks a new branch with the edited
    content.
    """

    message_id: uuid.UUID
    new_content: str


class RegenerateMessageRequest(BaseModel):
    """Regenerate an assistant message in place.

    If ``message_id`` is omitted the backend regenerates the latest assistant
    turn. Otherwise the named assistant message is replaced (its parent user
    message is replayed).
    """

    message_id: uuid.UUID | None = None


class SwitchBranchRequest(BaseModel):
    """Flip the active branch by checkpoint id.

    The backend can't truly "switch heads" in LangGraph (re-invocation is the
    only way to advance the timeline) so this endpoint just records the
    user's choice; subsequent edits will fork from this checkpoint.
    """

    checkpoint_id: str
