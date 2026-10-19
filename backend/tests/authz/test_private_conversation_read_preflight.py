"""Captured Conversation consumers refresh their actor before private root reads."""

from __future__ import annotations

from typing import Final, Literal, assert_never

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import RunConversationTarget, lock_run_conversation
from app.authz.conversation_scope import lock_private_conversation, require_private_conversation
from app.exceptions import AppError
from app.models.tool import Tool
from app.services import conversation_run_queue_service as queue
from tests.authz.org_context_fixture import org_fixture as org_fixture
from tests.authz.private_scope_fixture import private_scopes as private_scopes
from tests.authz.queue_resource_fixture import (
    QueueResourceCase,
    queue_resource_sql,
    rejected_scope,
    staged_queue_tool,
)
from tests.authz.queue_resource_fixture import queue_resource_case as queue_resource_case

type ReadOperation = Literal["required", "locked", "run", "queue", "list", "pause", "edit"]
type ActorRevocation = Literal["removed", "tenant_removed", "inactive_user"]

OPERATIONS: Final[tuple[ReadOperation, ...]] = (
    "required",
    "locked",
    "run",
    "queue",
    "list",
    "pause",
    "edit",
)
REVOCATIONS: Final[tuple[ActorRevocation, ...]] = ("removed", "tenant_removed", "inactive_user")


async def exercise_consumer(
    db: AsyncSession, case: QueueResourceCase, operation: ReadOperation
) -> bool:
    """Use each real root or consumer; an absent executable root represents denial."""
    scope = case.scope
    match operation:
        case "required":
            await require_private_conversation(db, scope.conversation_id, scope.context)
        case "locked":
            await lock_private_conversation(db, scope.conversation_id, scope.context)
        case "run":
            result = await lock_run_conversation(
                db, RunConversationTarget(scope.conversation_id, scope.agent_id, scope.context)
            )
            return result is not None
        case "queue":
            await queue.lock_owned_conversation(
                db, conversation_id=scope.conversation_id, context=scope.context
            )
        case "list":
            await queue.list_inputs(
                db, conversation_id=scope.conversation_id, context=scope.context
            )
        case "pause":
            await queue.pause_queue(
                db, conversation_id=scope.conversation_id, context=scope.context
            )
        case "edit":
            await queue.edit_pending_input(
                db,
                conversation_id=scope.conversation_id,
                input_id=case.queued.id,
                context=scope.context,
                expected_revision=1,
                input_payload={"messages": [{"role": "user", "content": "Accepted replacement"}]},
            )
        case unreachable:
            assert_never(unreachable)
    return True


@pytest.mark.parametrize(
    "case",
    [(op, change) for op in OPERATIONS for change in REVOCATIONS],
    ids=[f"{op}-{change}" for op in OPERATIONS for change in REVOCATIONS],
)
async def test_revoked_actor_precedes_private_conversation_or_input_reads(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
    case: tuple[ReadOperation, ActorRevocation],
) -> None:
    operation, revocation = case
    target = await rejected_scope(db, queue_resource_case, revocation)
    pending = staged_queue_tool(target)
    db.add(pending)
    accepted = False
    with queue_resource_sql(db) as statements:
        try:
            accepted = await exercise_consumer(db, target, operation)
        except (AppError, HTTPException) as error:
            match error:
                case AppError(status=status):
                    assert status == 404
                case HTTPException(status_code=status):
                    assert status == 404
                case unreachable:
                    assert_never(unreachable)
    assert not accepted
    assert pending in db.new
    assert not any(
        sql.lower().lstrip().startswith(("insert ", "update ", "delete ")) for sql in statements
    )
    assert not any(
        "conversations.runtime_policy_snapshot" in sql.lower()
        or "conversations.message_timestamps" in sql.lower()
        or "conversations.title" in sql.lower()
        or "conversation_run_inputs.input_payload" in sql.lower()
        for sql in statements
    )


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_accepted_conversation_consumers_preserve_caller_writes_and_queue_metadata(
    db: AsyncSession, queue_resource_case: QueueResourceCase, operation: ReadOperation
) -> None:
    target = queue_resource_case
    original_priority = target.queued.priority
    original_position = target.queued.position
    pending = staged_queue_tool(target)
    db.add(pending)
    with queue_resource_sql(db) as statements:
        assert await exercise_consumer(db, target, operation)
    assert any("from conversations" in sql.lower() for sql in statements)
    await db.flush()
    assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id
    assert target.queued.priority == original_priority
    assert target.queued.position == original_position
    match operation:
        case "edit":
            assert target.queued.revision == 2
        case "required" | "locked" | "run" | "queue" | "list" | "pause":
            assert target.queued.revision == 1
        case unreachable:
            assert_never(unreachable)
