"""Physical FK dependency closure and opaque checkpoint identity guard for m82.

The guard is deliberately conservative: it records every row in each table
reachable from the tracked roots, even rows currently belonging to another org.
Only primary identity and foreign/reference keys are persisted, never payloads.
"""

from typing import Final

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from schema_drafts.org_authz.backfill_snapshot import (
    JSON_ADAPTER,
    ROW_ADAPTER,
    DependencySnapshot,
    ForeignKeySnapshot,
)

CHECKPOINT_TABLES: Final = frozenset(
    {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
)
CHECKPOINT_KEYS: Final = (
    "thread_id",
    "checkpoint_ns",
    "checkpoint_id",
    "parent_checkpoint_id",
    "task_id",
    "idx",
    "channel",
    "version",
)


def capture(connection: Connection, roots: set[str]) -> tuple[DependencySnapshot, ...]:
    """Discover only physically present tables and traverse every incoming FK edge."""
    inspector = sa.inspect(connection)
    names = set(inspector.get_table_names())
    foreign_keys = {
        name: tuple(
            sorted(
                (
                    ForeignKeySnapshot.model_validate(raw)
                    for raw in inspector.get_foreign_keys(name)
                ),
                key=lambda fk: (
                    fk.constrained_columns,
                    fk.referred_table,
                    fk.referred_columns,
                    fk.referred_schema or "",
                ),
            )
        )
        for name in sorted(names)
    }
    reachable = (roots | CHECKPOINT_TABLES) & names
    while True:
        children = {
            name
            for name in names
            if any(fk.referred_table in reachable for fk in foreign_keys[name])
        }
        if children <= reachable:
            break
        reachable |= children
    metadata = sa.MetaData()
    snapshots: list[DependencySnapshot] = []
    for name in sorted(reachable):
        table = sa.Table(name, metadata, autoload_with=connection, resolve_fks=False)
        primary_key = tuple(column.name for column in table.primary_key)
        if not primary_key:
            raise ValueError(f"m82 dependent table has no stable primary identity: {name}")
        columns = tuple(
            dict.fromkeys(
                (
                    *primary_key,
                    *(key for fk in foreign_keys[name] for key in fk.constrained_columns),
                    *(
                        key
                        for key in CHECKPOINT_KEYS
                        if name in CHECKPOINT_TABLES and key in table.c
                    ),
                )
            )
        )
        query = sa.select(*(table.c[key] for key in columns)).order_by(*table.primary_key.columns)
        snapshots.append(
            DependencySnapshot(
                name=name,
                primary_key=primary_key,
                columns=columns,
                foreign_keys=foreign_keys[name],
                expected=[
                    JSON_ADAPTER.validate_json(ROW_ADAPTER.dump_json(dict(row)))
                    for row in connection.execute(query).mappings()
                ],
            )
        )
    return tuple(snapshots)


def verify(
    connection: Connection,
    recorded: tuple[DependencySnapshot, ...],
    roots: set[str],
) -> None:
    """Reject new/deleted identities, reparented refs or changed dependency contracts."""
    current = capture(connection, roots)
    expected = {item.name: item for item in recorded}
    if len(expected) != len(recorded):
        raise ValueError("m82 dependency snapshot contains duplicate tables")
    actual = {item.name: item for item in current}
    if set(expected) != set(actual):
        difference = ", ".join(sorted(set(expected) ^ set(actual)))
        raise ValueError(f"m82 dependency inventory mismatch: {difference}")
    for name, item in actual.items():
        if expected[name] != item:
            raise ValueError(f"m82 dependent identity/reference state changed: {name}")
