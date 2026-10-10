"""Administrative membership state never deletes the global user account."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MemberCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: UUID


class MemberPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["active", "suspended"]


class MemberView(BaseModel):
    model_config = ConfigDict(frozen=True)
    user_id: UUID
    display_name: str
    email: str | None
    status: Literal["active", "suspended", "removed"]
    joined_at: datetime
    removed_at: datetime | None
