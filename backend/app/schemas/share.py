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

from app.schemas.conversation import MessageResponse, UtcDatetime


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
    """Public read-only conversation snapshot returned by ``/api/shares/{token}``."""

    share_token: str
    conversation_title: str | None = None
    agent: SharedAgentBrief
    messages: list[MessageResponse]
    shared_at: UtcDatetime
