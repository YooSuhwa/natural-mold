"""Agent responses outside ``AUTHZ_MODE=legacy`` (plan 9.1, 10.3, S20).

Legacy mode keeps returning the unchanged ``AgentResponse``/``AgentSummaryResponse``
bodies. Shadow and enforce modes add ``my_permissions``; a viewer without
``can_view_config`` receives ``AgentRunnerView`` (no settings, prompts, tools or traces).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.agent import AgentResponse, AgentSummaryResponse


class AgentAccessResponse(AgentResponse):
    my_permissions: list[str]


class AgentSummaryAccessResponse(AgentSummaryResponse):
    my_permissions: list[str]


class AgentRunnerView(BaseModel):
    """Runner summary: name, description, icon, opener questions, owner, my level."""

    view: Literal["runner"] = "runner"
    id: uuid.UUID
    name: str
    description: str | None = None
    image_url: str | None = None
    opener_questions: list[str] | None = None
    owner_id: uuid.UUID
    is_favorite: bool = False
    unread_count: int = 0
    my_level: str | None  # consumer | viewer | editor | manager (plan 10.1)
    my_permissions: list[str]


# Left to right: the richest variant first, the unchanged legacy body last, so a
# legacy ``AgentResponse`` never validates as (or serializes like) another variant.
AgentDetail = Annotated[
    AgentAccessResponse | AgentRunnerView | AgentResponse, Field(union_mode="left_to_right")
]
AgentListItem = AgentDetail
AgentSummaryItem = Annotated[
    AgentSummaryAccessResponse | AgentSummaryResponse, Field(union_mode="left_to_right")
]
