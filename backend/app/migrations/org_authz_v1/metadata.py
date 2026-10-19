"""Physical metadata adapter with frozen portable UUID semantics."""

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.migrations.org_authz_v1.contracts import MigrationContext
from app.migrations.org_authz_v1.uuid_manifest import UUID_COLUMNS


def reflect_metadata(connection: Connection) -> sa.MetaData:
    metadata = sa.MetaData()
    metadata.reflect(bind=connection)
    if connection.dialect.name == "sqlite":
        for name, columns in UUID_COLUMNS.items():
            table = metadata.tables.get(name)
            if table is not None:
                for column in columns:
                    if column in table.c:
                        table.c[column].type = sa.Uuid()
    for table in metadata.tables.values():
        if "updated_at" in table.c:
            table.c.updated_at.onupdate = None
    return metadata


def migration_context(connection: Connection) -> MigrationContext:
    return MigrationContext(connection, reflect_metadata(connection))
