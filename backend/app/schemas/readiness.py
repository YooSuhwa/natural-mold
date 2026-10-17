"""Readiness check bodies (plan 12.3; G-01).

``id``/``name``/``agent_id``/``credential`` are left out for items the caller may not see
(12.3: counts only); ``count`` is set on those aggregated items instead.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

ReadinessResult = Literal[
    "ready", "no_permission", "connect_by_me", "owner_must_connect", "unavailable"
]
ReadinessOverall = Literal["ready", "needs_connection", "limited", "blocked"]
ReadinessKind = Literal["llm_model", "tool", "mcp_server", "skill", "sub_agent"]


class ReadinessCredential(BaseModel):
    mode: str
    status: str  # active | auth_needed | expired | missing | unusable
    credential_id: uuid.UUID | None = None  # only the caller's own credential
    definition_key: str | None = None  # connect_by_me only: the type the caller connects


class ReadinessAction(BaseModel):
    type: Literal["request_access", "notify_owner", "connect", "oauth_start"]
    via: str | None = None


class ReadinessItemResponse(BaseModel):
    kind: ReadinessKind
    required: bool
    result: ReadinessResult
    id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    name: str | None = None
    count: int | None = None
    credential: ReadinessCredential | None = None
    action: ReadinessAction | None = None


class AgentReadinessResponse(BaseModel):
    agent_id: uuid.UUID | None = None  # only for a caller who may view the agent's settings
    overall: ReadinessOverall
    identity_mode: str
    items: list[ReadinessItemResponse]


class OwnerNotifyResponse(BaseModel):
    notified: int  # notices written (merged per credential for 24 hours); 0 = none


class ReadinessSummaryRequest(BaseModel):
    agent_ids: list[uuid.UUID] = Field(max_length=50)


class ReadinessSummary(BaseModel):
    overall: ReadinessOverall
    counts: dict[ReadinessResult, int]


__all__ = [
    "AgentReadinessResponse",
    "OwnerNotifyResponse",
    "ReadinessAction",
    "ReadinessCredential",
    "ReadinessItemResponse",
    "ReadinessSummary",
    "ReadinessSummaryRequest",
]
