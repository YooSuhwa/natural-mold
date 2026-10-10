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

from schema_drafts.org_authz.scope_columns import M81_SCOPED

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


class BackfillSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: Literal["m82_default_org_backfill"] = "m82_default_org_backfill"
    state: Literal["completed"] = "completed"
    tenant_id: UUID
    org_id: UUID
    tables: tuple[TableSnapshot, ...]


def preserve_legacy_timestamps(metadata: sa.MetaData) -> None:
    """m82 changes scope/ownership, never content modification timestamps.

    This metadata is a migration-local copy, so suppressing Python ORM onupdate
    callbacks does not change application writes or later migration behavior.
    """
    for name in (*M81_SCOPED, "users"):
        table = metadata.tables[name]
        if "updated_at" in table.c:
            table.c.updated_at.onupdate = None


def lock_writers(connection: Connection) -> None:
    """Keep PostgreSQL drift checks and subsequent writes in one stable transaction."""
    if connection.dialect.name == "postgresql":
        quote = connection.dialect.identifier_preparer.quote
        tables = ", ".join(quote(name) for name in sorted(sa.inspect(connection).get_table_names()))
        connection.execute(sa.text(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE"))


def tracked_columns(metadata: sa.MetaData) -> dict[str, tuple[str, ...]]:
    """Track all values m82 can modify, plus exact identities of its new rows."""
    columns: dict[str, tuple[str, ...]] = dict.fromkeys(M81_SCOPED, ("id", "tenant_id", "org_id"))
    for name in M81_SCOPED:
        table = metadata.tables[name]
        owner = next(key for key in ("user_id", "owner_user_id", "created_by") if key in table.c)
        columns[name] += (owner,)
        columns[name] += tuple(key for key in ("is_system", "agent_id") if key in table.c)
    columns["credentials"] += ("scope",)
    columns["users"] = ("id", "last_active_org_id")
    columns.update({name: tuple(metadata.tables[name].c.keys()) for name in CREATED})
    return columns


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


def capture(
    connection: Connection, metadata: sa.MetaData, tenant: UUID, org: UUID
) -> BackfillSnapshot:
    """Capture before/after values using the same table/column contract."""
    return BackfillSnapshot(
        tenant_id=tenant,
        org_id=org,
        tables=tuple(
            TableSnapshot(
                name=name,
                columns=columns,
                original=[],
                expected=rows(
                    connection,
                    metadata.tables[name],
                    columns,
                    tenant,
                    org,
                ),
            )
            for name, columns in tracked_columns(metadata).items()
        ),
    )


def default_references(
    connection: Connection,
    metadata: sa.MetaData,
    tenant: UUID,
    org: UUID,
    *,
    allowed_tables: set[str],
) -> None:
    """Do not rely on enabled foreign keys for post-migration dependency detection."""
    inspector = sa.inspect(connection)
    for name in inspector.get_table_names():
        if name in allowed_tables:
            continue
        names = {column["name"] for column in inspector.get_columns(name)}
        targets = {"tenant_id": tenant, "org_id": org, "last_active_org_id": org}
        if name == "tenants":
            targets["id"] = tenant
        elif name == "organizations":
            targets["id"] = org
        matching = {key: value for key, value in targets.items() if key in names}
        if not matching:
            continue
        table = metadata.tables.get(name)
        if table is None or not set(matching).issubset(table.c.keys()):
            table = sa.Table(name, sa.MetaData(), autoload_with=connection)
        predicates = [
            sa.type_coerce(table.c[key], sa.Uuid()) == value for key, value in matching.items()
        ]
        if connection.scalar(
            sa.select(sa.literal(1)).where(sa.or_(*predicates)).select_from(table).limit(1)
        ):
            raise ValueError(f"m82 default scope has untracked dependencies: {name}")


def verify(
    connection: Connection,
    metadata: sa.MetaData,
    snapshot: BackfillSnapshot,
    tenant: UUID,
    org: UUID,
) -> None:
    """Fail before writes unless both the snapshot contract and completed state match."""
    expected_columns = tracked_columns(metadata)
    if snapshot.tenant_id != tenant or snapshot.org_id != org:
        raise ValueError("m82 snapshot default identity mismatch")
    if {item.name: item.columns for item in snapshot.tables} != expected_columns:
        raise ValueError("m82 snapshot table/column contract mismatch")
    if len(snapshot.tables) != len(expected_columns):
        raise ValueError("m82 snapshot contains duplicate tables")
    for item in snapshot.tables:
        if rows(connection, metadata.tables[item.name], item.columns, tenant, org) != item.expected:
            raise ValueError(f"m82 completed state changed: {item.name}")
        table = metadata.tables[item.name]
        if any(set(row) != set(item.columns) for row in (*item.original, *item.expected)):
            raise ValueError(f"m82 snapshot row/column contract mismatch: {item.name}")
        for row in (*item.original, *item.expected):
            native_values(table, row)
            if item.name == "credentials" and row["scope"] not in (
                "personal",
                "tenant",
                "platform",
            ):
                raise ValueError("m82 snapshot credential scope malformed")
        original_keys = [tuple(row[key.name] for key in table.primary_key) for row in item.original]
        expected_keys = [tuple(row[key.name] for key in table.primary_key) for row in item.expected]
        if item.name in CREATED:
            if item.original:
                raise ValueError(f"m82 snapshot adopts preexisting defaults: {item.name}")
        elif original_keys != expected_keys:
            raise ValueError(f"m82 snapshot original identity mismatch: {item.name}")
    default_references(connection, metadata, tenant, org, allowed_tables=set(expected_columns))


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


def restore(connection: Connection, metadata: sa.MetaData, snapshot: BackfillSnapshot) -> None:
    """Restore only recorded original columns and delete only recorded created identities."""
    for item in snapshot.tables:
        if item.name in CREATED:
            continue
        table = metadata.tables[item.name]
        for original, expected in zip(item.original, item.expected, strict=True):
            changed = {key: value for key, value in original.items() if value != expected[key]}
            if not changed:
                continue
            values = native_values(table, changed)
            connection.execute(
                sa.update(table).where(table.c.id == UUID(str(original["id"]))).values(**values)
            )
    snapshots = {item.name: item for item in snapshot.tables}
    for name in reversed(CREATED):
        table = metadata.tables[name]
        for row in snapshots[name].expected:
            connection.execute(
                sa.delete(table).where(
                    sa.and_(
                        *(column == UUID(str(row[column.name])) for column in table.primary_key)
                    )
                )
            )
