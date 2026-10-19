"""Opaque LangGraph state without app FKs must block rollback when keys change."""

from typing import Literal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz.backfill_snapshot import JSON_ADAPTER
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.test_backfill_dependents import assert_scopes_preserved
from tests.authz.test_default_backfill_rollback import prepared_connection as prepared_connection
from tests.authz.test_schema_draft_migrations import execute

CheckpointName = Literal[
    "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"
]


def checkpoint_table(name: CheckpointName) -> sa.Table:
    """Use the installed checkpoint-postgres saver's actual primary-key contracts."""
    keys = {
        "checkpoints": ("thread_id", "checkpoint_ns", "checkpoint_id"),
        "checkpoint_blobs": ("thread_id", "checkpoint_ns", "channel", "version"),
        "checkpoint_writes": ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        "checkpoint_migrations": ("v",),
    }
    columns: list[sa.Column[int] | sa.Column[str] | sa.Column[bytes]] = []
    for key in keys[name]:
        if key in ("idx", "v"):
            columns.append(sa.Column(key, sa.Integer(), primary_key=True))
        else:
            columns.append(sa.Column(key, sa.String(), primary_key=True))
    columns.append(sa.Column("private_payload", sa.LargeBinary()))
    if name == "checkpoints":
        columns.append(sa.Column("parent_checkpoint_id", sa.String()))
    return sa.Table(name, sa.MetaData(), *columns)


@pytest.mark.parametrize(
    "name",
    ["checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"],
)
async def test_new_optional_checkpoint_identity_blocks_rollback(
    prepared_connection: AsyncConnection,
    name: CheckpointName,
) -> None:
    # Given: an optional physical checkpoint table exists before the migration.
    connection = prepared_connection
    table = checkpoint_table(name)
    await connection.run_sync(table.create)
    original = {
        column.name: (1 if column.name in ("idx", "v") else "original")
        for column in table.primary_key
    }
    await connection.execute(
        sa.insert(table).values(**original, private_payload=b"checkpoint-secret")
    )
    await connection.run_sync(execute, m82.upgrade)
    tenant = schema_metadata(scope_columns=True).tables["tenants"]
    settings = JSON_ADAPTER.validate_python(await connection.scalar(sa.select(tenant.c.settings)))
    assert b"checkpoint-secret" not in JSON_ADAPTER.dump_json(settings)
    added = {
        column.name: (2 if column.name in ("idx", "v") else "new") for column in table.primary_key
    }
    await connection.execute(sa.insert(table).values(**added))
    # When: post-m82 graph state has no declared FK but does have a new identity.
    with pytest.raises(ValueError, match=f"m82.*{name}"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: rollback preserves original company scope and both checkpoint rows.
    await assert_scopes_preserved(connection)
    assert await connection.scalar(sa.select(sa.func.count()).select_from(table)) == 2


async def test_reparented_checkpoint_key_blocks_rollback(
    prepared_connection: AsyncConnection,
) -> None:
    # Given: checkpoint identity is unchanged but its parent checkpoint reference changes.
    connection = prepared_connection
    table = checkpoint_table("checkpoints")
    await connection.run_sync(table.create)
    await connection.execute(
        sa.insert(table).values(
            thread_id="thread",
            checkpoint_ns="",
            checkpoint_id="checkpoint",
            parent_checkpoint_id="original-parent",
        )
    )
    await connection.run_sync(execute, m82.upgrade)
    await connection.execute(sa.update(table).values(parent_checkpoint_id="new-parent"))
    # When: only reference keys, rather than payload bytes, are compared.
    with pytest.raises(ValueError, match="m82.*checkpoints"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: no parent scope is changed by the rejected rollback.
    await assert_scopes_preserved(connection)
    assert await connection.scalar(sa.select(table.c.parent_checkpoint_id)) == "new-parent"
