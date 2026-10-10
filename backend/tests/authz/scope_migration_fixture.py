"""Execute live revisions with frozen legacy insertion defaults in owned databases."""

from datetime import UTC, datetime
from sqlite3 import Connection as SQLiteConnection
from typing import Protocol, runtime_checkable

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from schema_drafts.org_authz.schema import baseline_metadata

SCOPE_REVISIONS = (
    "m81_org_scope_columns",
    "m82_default_org_backfill",
    "m83_org_scope_not_null",
)


@runtime_checkable
class RevisionOperations(Protocol):
    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


def live_revision(name: str) -> RevisionOperations:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(name)
    assert revision is not None
    module = revision.module
    assert isinstance(module, RevisionOperations)
    return module


def execute(connection: Connection, name: str, *, downgrade: bool = False) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        module = live_revision(name)
        if downgrade:
            module.downgrade()
        else:
            module.upgrade()


def prepare_m80(connection: Connection) -> None:
    # Live organization DDL uses PostgreSQL now(); SQLite needs an explicit test adapter.
    driver = connection.connection.driver_connection
    if isinstance(driver, SQLiteConnection):
        driver.create_function("now", 0, lambda: datetime.now(UTC).isoformat())
    baseline_metadata().create_all(connection)
    for name in (
        "m78_tenants_organizations",
        "m79_groups",
        "m80_org_capabilities_invitations",
    ):
        execute(connection, name)

    if isinstance(driver, SQLiteConnection):
        # Base.create_all omits m40's PostgreSQL-only partial indexes. Mirror their
        # physical semantics before recording the SQLite round-trip baseline.
        connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_marketplace_items_system_slug ON "
                "marketplace_items(resource_type, slug) WHERE is_system = true"
            )
        )
        connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_marketplace_items_owner_slug ON "
                "marketplace_items(owner_user_id, resource_type, slug) "
                "WHERE owner_user_id IS NOT NULL"
            )
        )


def relational_contract(connection: Connection) -> dict[str, object]:
    """Compare semantic constraints; legacy unnamed constraints acquire stable names."""
    inspector = sa.inspect(connection)
    result: dict[str, object] = {}
    for name in inspector.get_table_names():
        result[name] = {
            "columns": sorted(
                (column["name"], str(column["type"]), column["nullable"], column["default"])
                for column in inspector.get_columns(name)
            ),
            "pk": inspector.get_pk_constraint(name)["constrained_columns"],
            "fks": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk.get("options", {}).get("ondelete", ""),
                )
                for fk in inspector.get_foreign_keys(name)
            ),
            "unique": sorted(
                tuple(unique["column_names"]) for unique in inspector.get_unique_constraints(name)
            ),
            "checks": sorted(check["sqltext"] for check in inspector.get_check_constraints(name)),
            "indexes": sorted(
                (index["name"], tuple(index["column_names"]), bool(index["unique"]))
                for index in inspector.get_indexes(name)
                if not index.get("duplicates_constraint")
            ),
        }
    return result


def legacy_rows(
    connection: Connection, columns: dict[str, tuple[str, ...]]
) -> dict[str, list[dict[str, object]]]:
    """Record whole legacy rows, including content/ciphertext/timestamps, only in tests."""
    metadata = sa.MetaData()
    metadata.reflect(bind=connection)
    result: dict[str, list[dict[str, object]]] = {}
    for name, keys in columns.items():
        table = metadata.tables[name]
        query = sa.select(*(table.c[key] for key in keys)).order_by(*table.primary_key.columns)
        result[name] = [dict(row) for row in connection.execute(query).mappings()]
    return result
