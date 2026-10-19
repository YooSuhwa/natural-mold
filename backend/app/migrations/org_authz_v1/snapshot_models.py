"""Migration-owned m82 provenance; verify a complete snapshot before any rollback write.

The reserved tenant-settings entry is written only by this migration, never read
from tenant configuration or a user request. It is not an authorization receipt.
Missing, malformed or drifted provenance requires operator reconciliation.
"""

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

KEY: Final = "_migration_m82"
CREATED: Final = (
    "tenants",
    "organizations",
    "tenant_members",
    "organization_members",
    "tenant_role_grants",
    "organization_role_grants",
)
ROW_ADAPTER = TypeAdapter(dict[str, JsonValue | UUID | datetime | Decimal])
JSON_ADAPTER = TypeAdapter(dict[str, JsonValue])


class TableSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    columns: tuple[str, ...]
    original: list[dict[str, JsonValue]]
    expected: list[dict[str, JsonValue]]


class ForeignKeySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    constrained_columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    referred_schema: str | None = None


class DependencySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    primary_key: tuple[str, ...]
    columns: tuple[str, ...]
    foreign_keys: tuple[ForeignKeySnapshot, ...]
    expected: list[dict[str, JsonValue]]


class BackfillSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: Literal["m82_default_org_backfill"] = "m82_default_org_backfill"
    state: Literal["completed"] = "completed"
    tenant_id: UUID
    org_id: UUID
    tables: tuple[TableSnapshot, ...]
    version: Literal[2]
    dependencies: tuple[DependencySnapshot, ...]
