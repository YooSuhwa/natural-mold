"""Frozen v2 provenance IO and portable cell conversion."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import JsonValue, ValidationError

from app.migrations.org_authz_v1.contracts import MigrationContext
from app.migrations.org_authz_v1.snapshot_models import (
    CREATED,
    JSON_ADAPTER,
    KEY,
    ROW_ADAPTER,
    BackfillSnapshot,
)


def rows(
    context: MigrationContext,
    table: sa.Table,
    columns: tuple[str, ...],
) -> list[dict[str, JsonValue]]:
    """Normalize DB UUID/timestamp/numeric values to portable JSON."""
    connection, tenant, org = (
        context.connection,
        context.identity.tenant_id,
        context.identity.org_id,
    )
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


def load(context: MigrationContext) -> BackfillSnapshot | None:
    """An existing default UUID without valid migration provenance is a collision."""
    connection, metadata, tenant = context.connection, context.metadata, context.identity.tenant_id
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
    context: MigrationContext,
    before: BackfillSnapshot,
    after: BackfillSnapshot,
) -> None:
    """Persist a completed snapshot inside the newly created default tenant only."""
    connection, metadata = context.connection, context.metadata
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
