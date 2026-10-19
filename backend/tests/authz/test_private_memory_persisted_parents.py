"""Independent exact-source memory graph controls and adverse persisted parent cases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import MemoryRecord
from app.schemas.memory import MemoryRecordCreate, MemoryRecordUpdate
from app.services import memory_service
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("edge", ["agent", "conversation"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_memory_update_requires_complete_persisted_parent_graph(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    edge: Literal["agent", "conversation"],
    foreign: bool,
) -> None:
    own = private_scopes.first
    first_conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    second_conversation = await create_conversation(
        db, api_case.second.agent_id, context=private_scopes.second
    )
    record = await memory_service.create_memory_record(
        db,
        context=own,
        payload=MemoryRecordCreate(
            scope="agent",
            agent_id=api_case.first.agent_id,
            source_conversation_id=first_conversation.id,
            content="F1_R12_BEFORE",
        ),
    )
    assert record is not None
    if foreign:
        match edge:
            case "agent":
                stmt = (
                    update(MemoryRecord)
                    .where(MemoryRecord.id == record.id)
                    .values(agent_id=api_case.second.agent_id)
                )
            case "conversation":
                stmt = (
                    update(MemoryRecord)
                    .where(MemoryRecord.id == record.id)
                    .values(source_conversation_id=second_conversation.id)
                )
            case unreachable:
                assert_never(unreachable)
        await db.execute(stmt.execution_options(synchronize_session=False))
        await db.commit()
    result = await memory_service.update_memory_record(
        db, memory_id=record.id, context=own, payload=MemoryRecordUpdate(content="F1_R12_AFTER")
    )
    actual = await db.scalar(select(MemoryRecord.content).where(MemoryRecord.id == record.id))
    assert (
        (result is None and actual == "F1_R12_BEFORE")
        if foreign
        else (result is not None and actual == "F1_R12_AFTER")
    )


@pytest.mark.parametrize("foreign", [False, True])
async def test_runtime_memory_hydration_rejects_foreign_source_parent(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase, foreign: bool
) -> None:
    own = private_scopes.first
    own_conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    foreign_conversation = await create_conversation(
        db, api_case.second.agent_id, context=private_scopes.second
    )
    record = await memory_service.create_memory_record(
        db,
        context=own,
        payload=MemoryRecordCreate(
            scope="user",
            content="F1_R12_RUNTIME_MEMORY",
            source_conversation_id=own_conversation.id,
        ),
    )
    assert record is not None
    if foreign:
        await db.execute(
            update(MemoryRecord)
            .where(MemoryRecord.id == record.id)
            .values(source_conversation_id=foreign_conversation.id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    rows = await memory_service.list_runtime_memory_records(
        db, context=own, agent_id=api_case.first.agent_id, allowed_scopes="both"
    )
    observed = any(row.id == record.id for row in rows)
    assert observed != foreign
