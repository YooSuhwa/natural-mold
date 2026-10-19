"""Bounded principal search supports names and controlled email matching."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


class PrincipalSearchQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    types: Annotated[
        str, StringConstraints(pattern=r"^(user|group|org)(,(user|group|org))*$", max_length=64)
    ] = "user,group,org"


class PrincipalSearchView(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["user", "group", "org"]
    id: UUID
    display_name: str
    email: str | None = None
    member_count: int | None = None
