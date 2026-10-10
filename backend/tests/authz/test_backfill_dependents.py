"""Rollback must inventory FK descendants without recording their private payloads."""

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz.default_org_backfill import ORG, TENANT
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.test_default_backfill_rollback import (
    migrated_connection as migrated_connection,
)
from tests.authz.test_default_backfill_rollback import (
    prepared_connection as prepared_connection,
)
from tests.authz.test_schema_draft_migrations import execute


async def add_event(connection: AsyncConnection, conversation: UUID) -> UUID:
    table = schema_metadata().tables["message_events"]
    key = uuid4()
    await connection.execute(
        sa.insert(table).values(
            id=key,
            conversation_id=conversation,
            assistant_msg_id=str(key),
            events=[{"private_body": "dependent-private-message"}],
        )
    )
    return key


async def add_chunk(connection: AsyncConnection, conversation: UUID, event: UUID) -> UUID:
    table = schema_metadata().tables["message_event_chunks"]
    key = uuid4()
    await connection.execute(
        sa.insert(table).values(
            id=key,
            conversation_id=conversation,
            message_event_id=event,
            assistant_msg_id=str(event),
            seq_start=0,
            seq_end=0,
            events=[{"private_body": "dependent-private-chunk"}],
        )
    )
    return key


async def assert_scopes_preserved(connection: AsyncConnection) -> None:
    conversations = schema_metadata(scope_columns=True).tables["conversations"]
    rows = (
        await connection.execute(sa.select(conversations.c.org_id, conversations.c.tenant_id))
    ).all()
    assert rows == [(ORG, TENANT)] * 4


async def test_new_event_blocks_rollback_before_parent_scope_is_cleared(
    migrated_connection: AsyncConnection,
) -> None:
    # Given: a post-m82 event only references an existing scoped conversation.
    connection = migrated_connection
    table = schema_metadata().tables["conversations"]
    conversation = await connection.scalar(sa.select(table.c.id))
    assert conversation is not None
    key = await add_event(connection, conversation)
    # When: rollback checks newly created FK-only children.
    with pytest.raises(ValueError, match="m82.*message_events"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: both event identity and every original parent scope remain intact.
    await assert_scopes_preserved(connection)
    assert (
        await connection.scalar(sa.select(schema_metadata().tables["message_events"].c.id)) == key
    )


async def test_new_chunk_under_existing_event_blocks_rollback(
    prepared_connection: AsyncConnection,
) -> None:
    # Given: an event exists before m82 and a chunk is appended after m82.
    connection = prepared_connection
    conversation = await connection.scalar(
        sa.select(schema_metadata().tables["conversations"].c.id)
    )
    assert conversation is not None
    event = await add_event(connection, conversation)
    await connection.run_sync(execute, m82.upgrade)
    key = await add_chunk(connection, conversation, event)
    # When: rollback verifies descendants beyond the root table.
    with pytest.raises(ValueError, match="m82.*message_event_chunks"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: the appended chunk and original scope are preserved.
    await assert_scopes_preserved(connection)
    assert (
        await connection.scalar(sa.select(schema_metadata().tables["message_event_chunks"].c.id))
        == key
    )


async def test_new_agent_tool_link_blocks_rollback(
    prepared_connection: AsyncConnection,
) -> None:
    # Given: a post-m82 link attaches two existing scoped resources.
    connection = prepared_connection
    metadata = schema_metadata(scope_columns=True)
    tool_id = uuid4()
    owner = await connection.scalar(sa.select(metadata.tables["users"].c.id))
    agent = await connection.scalar(sa.select(metadata.tables["agents"].c.id))
    await connection.execute(
        sa.insert(metadata.tables["tools"]).values(
            id=tool_id,
            user_id=owner,
            name="Fixture tool",
            definition_key="http_request",
        )
    )
    await connection.run_sync(execute, m82.upgrade)
    await connection.execute(
        sa.insert(metadata.tables["agent_tools"]).values(agent_id=agent, tool_id=tool_id)
    )
    # When: rollback inventories non-conversation dependency identities.
    with pytest.raises(ValueError, match="m82.*agent_tools"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: the link and parent scope are retained.
    await assert_scopes_preserved(connection)
    assert (
        await connection.scalar(
            sa.select(sa.func.count()).select_from(metadata.tables["agent_tools"])
        )
        == 1
    )


@pytest.mark.parametrize("child", ["message_events", "message_event_chunks"])
async def test_reparented_original_dependent_blocks_rollback(
    prepared_connection: AsyncConnection,
    child: str,
) -> None:
    # Given: both event/chunk identities predate m82, then one parent reference changes.
    connection = prepared_connection
    metadata = schema_metadata()
    conversations = (
        await connection.scalars(sa.select(metadata.tables["conversations"].c.id))
    ).all()
    first, second = conversations[:2]
    first_event = await add_event(connection, first)
    second_event = await add_event(connection, second)
    chunk = await add_chunk(connection, first, first_event)
    await connection.run_sync(execute, m82.upgrade)
    table = metadata.tables[child]
    values = (
        {"conversation_id": second}
        if child == "message_events"
        else {"message_event_id": second_event}
    )
    await connection.execute(
        sa.update(table)
        .where(table.c.id == (first_event if child == "message_events" else chunk))
        .values(**values)
    )
    # When: rollback compares reference values rather than only row counts.
    with pytest.raises(ValueError, match=f"m82.*{child}"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: it does not reclassify any original parent as unscoped data.
    await assert_scopes_preserved(connection)
