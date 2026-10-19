"""Migration-owned m82 provenance; verify a complete snapshot before any rollback write.

The reserved tenant-settings entry is written only by this migration, never read
from tenant configuration or a user request. It is not an authorization receipt.
Missing, malformed or drifted provenance requires operator reconciliation.
"""

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from sqlalchemy.engine import Connection

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


def rows(
    connection: Connection,
    table: sa.Table,
    columns: tuple[str, ...],
    tenant: UUID,
    org: UUID,
) -> list[dict[str, JsonValue]]:
    """Normalize DB UUID/timestamp/numeric values to portable JSON."""
    query = sa.select(*(table.c[name] for name in columns)).order_by(*table.primary_key.columns)
    if table.name in CREATED:
        if table.name == "tenants":
            query = query.where(table.c.id == tenant)
        elif table.name == "organizations":
            query = query.where(table.c.tenant_id == tenant)
        elif "org_id" in table.c:
            query = query.where(table.c.org_id == org)
        else:
            query = query.where(table.c.tenant_id == tenant)
    result: list[dict[str, JsonValue]] = []
    for row in connection.execute(query).mappings():
        normalized = JSON_ADAPTER.validate_json(ROW_ADAPTER.dump_json(dict(row)))
        if table.name == "tenants":
            settings = JSON_ADAPTER.validate_python(normalized["settings"])
            settings.pop(KEY, None)
            normalized["settings"] = settings
        result.append(normalized)
    return result


def load(connection: Connection, metadata: sa.MetaData, tenant: UUID) -> BackfillSnapshot | None:
    """An existing default UUID without valid migration provenance is a collision."""
    table = metadata.tables["tenants"]
    raw = connection.execute(sa.select(table.c.settings).where(table.c.id == tenant)).first()
    if raw is None:
        return None
    settings = JSON_ADAPTER.validate_python(raw.settings)
    if KEY not in settings:
        raise ValueError("m82 default tenant collision: migration snapshot unavailable")
    try:
        return BackfillSnapshot.model_validate(settings[KEY])
    except ValidationError as error:
        raise ValueError("m82 migration snapshot malformed") from error


def save(
    connection: Connection,
    metadata: sa.MetaData,
    before: BackfillSnapshot,
    after: BackfillSnapshot,
) -> None:
    """Persist a completed snapshot inside the newly created default tenant only."""
    originals = {item.name: item.expected for item in before.tables}
    snapshot = after.model_copy(
        update={
            "tables": tuple(
                item.model_copy(update={"original": originals[item.name]}) for item in after.tables
            )
        }
    )
    connection.execute(
        sa.update(metadata.tables["tenants"])
        .where(
            metadata.tables["tenants"].c.id == after.tenant_id,
        )
        .values(
            settings={KEY: snapshot.model_dump(mode="json")},
            updated_at=metadata.tables["tenants"].c.updated_at,
        )
    )


def native_values(
    table: sa.Table, values: dict[str, JsonValue]
) -> dict[str, JsonValue | UUID | datetime]:
    """Parse saved migration cells before any restoration writes are allowed."""
    result: dict[str, JsonValue | UUID | datetime] = {}
    for key, value in values.items():
        if value is not None and isinstance(table.c[key].type, sa.Uuid):
            result[key] = UUID(str(value))
        elif value is not None and isinstance(table.c[key].type, sa.DateTime):
            result[key] = datetime.fromisoformat(str(value))
        else:
            result[key] = value
    return result
