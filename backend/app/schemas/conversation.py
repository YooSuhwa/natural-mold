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


class MessageCreate(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    created_at: UtcDatetime
