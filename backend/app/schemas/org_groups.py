"""Organization directory pages and manual group edits."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DirectoryQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=50, ge=1, le=200)


class DirectoryPage[Entry: BaseModel](BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[Entry]
    next_cursor: str | None
    has_more: bool
    updated_at: datetime


class GroupCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: GroupName
    description: str | None = Field(default=None, max_length=5000)


class GroupPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: GroupName | None = None
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def explicit_name_is_text(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name cannot be null")
        return value


class GroupView(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)
    id: UUID
    org_id: UUID
    name: str
    description: str | None
    source: Literal["manual", "idp"]
    created_at: datetime
    updated_at: datetime


class GroupMemberCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: UUID


class GroupMemberView(BaseModel):
    model_config = ConfigDict(frozen=True)
    user_id: UUID
    display_name: str
    email: str | None
    added_at: datetime
