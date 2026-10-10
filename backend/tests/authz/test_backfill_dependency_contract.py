"""Dependency snapshots must use the physical schema and reject incomplete provenance."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncConnection

from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz.backfill_snapshot import JSON_ADAPTER, KEY
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.test_backfill_dependents import add_chunk, add_event, assert_scopes_preserved
from tests.authz.test_default_backfill_rollback import (
    migrated_connection as migrated_connection,
)
from tests.authz.test_default_backfill_rollback import (
    prepared_connection as prepared_connection,
)
from tests.authz.test_schema_draft_migrations import execute


async def test_new_third_hop_physical_child_blocks_rollback(
    prepared_connection: AsyncConnection,
) -> None:
    # Given: a physical-only table reaches conversations through chunks then events.
    connection = prepared_connection
    metadata = schema_metadata()
    descendant = sa.Table(
        "physical_event_refs",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("message_event_chunks.id")),
        sa.Column("private_payload", sa.Text()),
    )
    await connection.run_sync(descendant.create)
    conversation = await connection.scalar(sa.select(metadata.tables["conversations"].c.id))
    assert conversation is not None
    event = await add_event(connection, conversation)
    chunk = await add_chunk(connection, conversation, event)
    await connection.run_sync(execute, m82.upgrade)
    key = uuid4()
    await connection.execute(
        sa.insert(descendant).values(
            id=key,
            chunk_id=chunk,
            private_payload="private-third-hop-payload",
        )
    )
    # When: rollback discovers real descendants absent from application metadata.
    with pytest.raises(ValueError, match="m82.*physical_event_refs"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: the third-hop row and its inherited scope survive the refused rollback.
    assert await connection.scalar(sa.select(descendant.c.id)) == key
    await assert_scopes_preserved(connection)


@pytest.mark.parametrize("corruption", ["missing", "omitted_child", "changed_ref_contract"])
async def test_incomplete_dependency_snapshot_aborts_before_writes(
    migrated_connection: AsyncConnection,
    corruption: str,
) -> None:
    # Given: reserved provenance is missing a whole inventory, a table, or a FK reference key.
    connection = migrated_connection
    tenant = schema_metadata(scope_columns=True).tables["tenants"]
    settings = JSON_ADAPTER.validate_python(await connection.scalar(sa.select(tenant.c.settings)))
    snapshot = JSON_ADAPTER.validate_python(settings[KEY])
    if corruption == "missing":
        snapshot.pop("dependencies")
    else:
        dependencies = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
            snapshot["dependencies"]
        )
        if corruption == "omitted_child":
            dependencies = [item for item in dependencies if item["name"] != "message_events"]
        else:
            for item in dependencies:
                if item["name"] == "message_events":
                    item["columns"] = ["id"]
        snapshot["dependencies"] = TypeAdapter(JsonValue).validate_python(dependencies)
    settings[KEY] = snapshot
    await connection.execute(sa.update(tenant).values(settings=settings))
    # When: rollback cannot prove a complete physical dependency snapshot.
    with pytest.raises(ValueError, match="m82"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: no original parent scope has been cleared.
    await assert_scopes_preserved(connection)


async def test_clean_rollback_keeps_original_children_without_storing_secret_payloads(
    prepared_connection: AsyncConnection,
) -> None:
    # Given: pre-m82 event/chunk payloads and encrypted credentials are private content.
    connection = prepared_connection
    metadata = schema_metadata(scope_columns=True)
    conversation = await connection.scalar(sa.select(metadata.tables["conversations"].c.id))
    assert conversation is not None
    event = await add_event(connection, conversation)
    chunk = await add_chunk(connection, conversation, event)
    await connection.run_sync(execute, m82.upgrade)
    settings = JSON_ADAPTER.validate_python(
        await connection.scalar(
            sa.select(metadata.tables["tenants"].c.settings),
        )
    )
    encoded = JSON_ADAPTER.dump_json(settings)
    assert b"dependent-private-message" not in encoded
    assert b"dependent-private-chunk" not in encoded
    assert b"fixture-ciphertext" not in encoded
    # When: clean rollback has verified all original identity/reference values.
    await connection.run_sync(execute, m82.downgrade)
    # Then: original dependent identities and their payloads remain unchanged.
    event_row = (await connection.execute(sa.select(metadata.tables["message_events"]))).one()
    chunk_row = (await connection.execute(sa.select(metadata.tables["message_event_chunks"]))).one()
    assert (event_row.id, chunk_row.id, chunk_row.message_event_id) == (event, chunk, event)
    assert event_row.events == [{"private_body": "dependent-private-message"}]
    assert chunk_row.events == [{"private_body": "dependent-private-chunk"}]
