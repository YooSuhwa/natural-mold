from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, PlainSerializer, model_validator


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
    unread_count: int = 0
    last_read_at: UtcDatetime | None = None
    last_unread_at: UtcDatetime | None = None
    last_activity_source: str = "user"
    created_at: UtcDatetime
    updated_at: UtcDatetime

    model_config = {"from_attributes": True}


class Decision(BaseModel):
    """단일 tool_call에 대한 인간 결정.

    LangChain ``HumanInTheLoopMiddleware``의 ``HITLResponse.decisions[i]``와
    동일 shape (1:1 매칭). router에서 검증한 뒤 ``model_dump(exclude_none=True)``
    로 dict 직렬화하여 ``Command(resume={"decisions": [dict, ...]})``로 송신한다
    (LangChain 미들웨어는 ``NotRequired`` TypedDict를 받음).

    - ``approve``: 추가 필드 없음.
    - ``edit``: ``edited_action={"name": str, "args": dict}`` 필수.
    - ``reject``: ``message`` 선택 (없으면 미들웨어가 기본 메시지 생성).
    - ``respond``: ``message`` 필수 (synthetic ToolMessage content).
    """

    type: Literal["approve", "edit", "reject", "respond"]
    edited_action: dict[str, Any] | None = None  # type=edit 시 필수
    message: str | None = None  # type=respond 시 필수, type=reject 시 선택

    @model_validator(mode="after")
    def _validate_payload_for_type(self) -> Decision:
        if self.type == "edit" and self.edited_action is None:
            raise ValueError("Decision(type='edit') requires 'edited_action'")
        if self.type == "respond" and self.message is None:
            raise ValueError("Decision(type='respond') requires 'message'")
        return self


class ResumeRequest(BaseModel):
    """HiTL resume 요청. LangChain ``HITLResponse`` 호환 표준 wire."""

    decisions: list[Decision]


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
    # W7-4 — conversation 단위 누적 비용 (USD). 메시지 단위는 model_id를 추적
    # 하지 않으므로 ``MessageResponse.usage.estimated_cost``를 채울 수 없는데,
    # ``token_usages`` 테이블이 turn마다 cost를 누적하므로 합산해 envelope에
    # 발행한다. 클라이언트의 Composer 토큰 바가 새로고침 후에도 cost를 표시.
    total_estimated_cost: float = 0.0


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


class TraceEvent(BaseModel):
    """Single SSE event captured during one assistant turn.

    Mirrors the shape ``stream_agent_response`` accumulates in ``trace_sink``:
    ``id`` is the SSE id ``"<msg_id>-<seq>"``, ``event`` is the event name
    (``message_start`` / ``content_delta`` / ``tool_call_start`` / ...), and
    ``data`` is the original JSON payload.
    """

    id: str | None = None
    event: str
    data: dict[str, Any]


class TurnTraceResponse(BaseModel):
    """One assistant turn's trace — events array + bookkeeping.

    Used by W6 (shared page chip rendering) and the future W3-out resume
    endpoint to read the full event sequence.

    ``linked_message_ids``: 이 turn에 노출된 assistant 메시지의 parsed UUID
    (``MessageResponse.id``와 동일 형식). 빈 배열/None이면 frontend는
    chronological turn 순서로 폴백.
    """

    assistant_msg_id: str
    events: list[TraceEvent]
    last_event_id: str | None
    linked_message_ids: list[str] | None = None
    created_at: UtcDatetime
    completed_at: UtcDatetime | None

    model_config = {"from_attributes": True}
