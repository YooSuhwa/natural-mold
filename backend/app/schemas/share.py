"""Pydantic schemas for share links + public conversation views.

Owner-facing endpoints expose ``ShareLinkResponse`` (token + lifecycle
metadata). Public visitor endpoints expose ``SharedConversationView``
(read-only conversation snapshot — agent identity, title, messages) which
deliberately omits ownership / debug fields so the public surface stays
minimal.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.schemas.conversation import MessageResponse, TurnTraceResponse, UtcDatetime


class ShareLinkResponse(BaseModel):
    """Owner-facing share link metadata (returned on create / fetch)."""

    id: uuid.UUID
    share_token: str
    conversation_id: uuid.UUID
    created_at: UtcDatetime
    revoked_at: UtcDatetime | None = None

    model_config = {"from_attributes": True}


class SharedAgentBrief(BaseModel):
    """Minimal agent identity for the public share header."""

    name: str
    description: str | None = None
    image_url: str | None = None


class SharedConversationView(BaseModel):
    """Public read-only conversation snapshot returned by ``/api/shares/{token}``.

    ``traces`` (W6): turn별 SSE event 시퀀스. 공개 페이지에서 도구/Skill 칩
    렌더에 사용된다. 빈 배열이면 trace가 없는 (W5 머지 이전에 만들어진) 대화.
    """

    share_token: str
    conversation_title: str | None = None
    conversation_created_at: UtcDatetime
    agent: SharedAgentBrief
    messages: list[MessageResponse]
    traces: list[TurnTraceResponse] = []
    shared_at: UtcDatetime
