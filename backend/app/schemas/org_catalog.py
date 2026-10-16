"""Organization and company catalog policy use the same explicit override format."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CatalogKind = Literal["model", "tool", "skill", "mcp_server"]


class CatalogOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: CatalogKind
    id: UUID
    enabled: bool


class CatalogPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    items: tuple[CatalogOverride, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def distinct_targets(self) -> "CatalogPolicy":
        if len({(item.type, item.id) for item in self.items}) != len(self.items):
            raise ValueError("catalog targets must be unique")
        return self

    def enabled_for(self, kind: CatalogKind, key: UUID) -> bool:
        return next(
            (item.enabled for item in self.items if item.type == kind and item.id == key), True
        )


class CatalogItemView(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: CatalogKind
    id: UUID
    name: str
    source: Literal["platform", "company"]
    company_enabled: bool
    organization_enabled: bool
    enabled: bool


class CatalogPolicyView(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[CatalogItemView, ...]
    updated_at: datetime


class CatalogImpactView(BaseModel):
    """What turning one item off affects in this organization (S09 confirmation)."""

    model_config = ConfigDict(frozen=True)
    type: CatalogKind
    id: UUID
    affected_agents: int
    required_agents: int
