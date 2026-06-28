from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ArtifactStatus = Literal["writing", "ready", "deleted", "failed"]
ArtifactKind = Literal[
    "image",
    "video",
    "audio",
    "pdf",
    "markdown",
    "html",
    "code",
    "document",
    "data",
    "cad",
    "other",
]
FileEventOperation = Literal["created", "updated", "deleted", "failed"]


class ArtifactSummary(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID
    assistant_msg_id: str
    run_id: str
    tool_call_id: str | None = None
    source_tool_name: str | None = None
    path: str
    display_name: str
    mime_type: str
    extension: str | None = None
    artifact_kind: ArtifactKind
    size_bytes: int
    sha256: str
    status: ArtifactStatus
    is_favorite: bool
    last_opened_at: datetime | None = None
    preview_count: int
    download_count: int
    version_id: uuid.UUID
    version_number: int
    created_at: datetime
    updated_at: datetime
    agent_name: str | None = None
    conversation_title: str | None = None
    url: str
    preview_url: str
    download_url: str
    # Real assistant message id(s) this artifact is linked to (parse_msg_id form,
    # matches the bubble anchor). ``assistant_msg_id`` above is the run id.
    linked_message_ids: list[str] | None = None


class FileEventPayload(ArtifactSummary):
    op: FileEventOperation


class ArtifactLibraryPage(BaseModel):
    items: list[ArtifactSummary]
    next_cursor: str | None = None
    has_more: bool = False


class ArtifactKindStat(BaseModel):
    kind: ArtifactKind
    count: int
    size_bytes: int


class ArtifactLibraryStats(BaseModel):
    total_count: int
    total_size_bytes: int
    favorite_count: int
    by_kind: list[ArtifactKindStat] = Field(default_factory=list)
    recent_count_7d: int


class ArtifactTextContent(BaseModel):
    text: str
    truncated: bool
    mime_type: str
    size_bytes: int


class ArtifactFavoriteUpdate(BaseModel):
    is_favorite: bool
