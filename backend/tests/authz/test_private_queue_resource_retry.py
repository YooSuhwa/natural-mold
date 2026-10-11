"""Retained queue snapshots require a current exact private Conversation parent."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, assert_never
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.tool import Tool
from app.models.user import User
from app.services import conversation_run_queue_service
from app.services.chat_resource_context_integration import (
    freeze_queued_resource_context_payload,
)
from app.services.chat_resource_context_payload import public_input_payload
from tests.authz.org_context_fixture import org_fixture as org_fixture
from tests.authz.private_scope_fixture import private_scopes as private_scopes
from tests.authz.queue_resource_fixture import (
    Denial,
    QueueResourceCase,
    queue_resource_sql,
    rejected_scope,
    snapshot_bytes,
    staged_queue_tool,
)
from tests.authz.queue_resource_fixture import queue_resource_case as queue_resource_case


@pytest.mark.parametrize(
    "denial",
    [
        "foreign_context",
        "wrong_company",
        "wrong_owner",
        "removed",
        "tenant_removed",
        "inactive_user",
        "moved_parent",
        "missing_parent",
        "scope_agent",
        "parent_agent",
        "orphan_parent",
        "child_agent",
        "child_owner",
        "request_parent",
        "moved_agent",
        "missing_agent",
    ],
)
async def test_retry_denies_before_snapshot_source_read_or_pending_write(
    db: AsyncSession, queue_resource_case: QueueResourceCase, denial: Denial
) -> None:
    case = await rejected_scope(db, queue_resource_case, denial)
    pending = staged_queue_tool(case)
    db.add(pending)
    denied = False
    with queue_resource_sql(db) as statements:
        try:
            await freeze_queued_resource_context_payload(db, case.scope, case.request)
        except (AppError, HTTPException) as exc:
            match exc:
                case AppError(status=status):
                    pass
                case HTTPException(status_code=status):
                    pass
                case unreachable:
                    assert_never(unreachable)
            assert status == 404
            denied = True
    print(f"QUEUE_RETRY denial={denial} denied={denied} sql={len(statements)}")
    assert denied
    assert pending in db.new
    assert not any(
        sql.lower().lstrip().startswith(("insert ", "update ", "delete ")) for sql in statements
    )
    assert not any("conversation_run_inputs.input_payload" in sql.lower() for sql in statements)
    assert not any("message_attachments" in sql.lower() for sql in statements)
    if denial in {"removed", "tenant_removed", "inactive_user"}:
        assert not any("from conversations" in sql.lower() for sql in statements)


@pytest.mark.parametrize("denial", ["foreign_context", "removed", "scope_agent", "request_parent"])
async def test_new_queue_request_uses_same_parent_guard(
    db: AsyncSession, queue_resource_case: QueueResourceCase, denial: Denial
) -> None:
    case = await rejected_scope(db, queue_resource_case, denial)
    request = replace(case.request, client_request_id="new-rejected-request")
    pending = staged_queue_tool(case)
    db.add(pending)
    with queue_resource_sql(db) as statements, pytest.raises((AppError, HTTPException)):
        await freeze_queued_resource_context_payload(db, case.scope, request)
    assert pending in db.new
    assert not any(
        sql.lower().lstrip().startswith(("insert ", "update ", "delete ")) for sql in statements
    )
    assert not any("message_attachments" in sql.lower() for sql in statements)


async def test_retry_keeps_snapshot_bytes_and_legitimate_staged_write(
    db: AsyncSession, queue_resource_case: QueueResourceCase
) -> None:
    case = queue_resource_case
    await AsyncPath(case.path).unlink()
    await db.execute(
        update(User)
        .where(User.id == case.scope.user_id)
        .values(last_active_org_id=case.scopes.second.org_id)
    )
    await db.commit()
    pending = staged_queue_tool(case)
    db.add(pending)
    payload = await freeze_queued_resource_context_payload(db, case.scope, case.request)
    assert snapshot_bytes(payload) == case.snapshot
    result = await conversation_run_queue_service.enqueue_input_with_result(
        db,
        conversation_id=case.scope.conversation_id,
        context=case.scope.context,
        client_request_id=case.request.client_request_id,
        source="chat",
        input_payload=payload,
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    assert result.created is False
    assert result.input.id == case.queued.id
    assert snapshot_bytes(result.input.input_payload) == case.snapshot
    assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id


@pytest.mark.parametrize("change", ["message", "reference", "remove_reference"])
async def test_changed_retry_returns_sanitized_input_for_existing_queue_conflict(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
    change: Literal["message", "reference", "remove_reference"],
) -> None:
    case = queue_resource_case
    incoming = dict(case.request.input_payload)
    match change:
        case "message":
            incoming["messages"] = [{"role": "user", "content": "Changed visible request"}]
        case "reference":
            incoming["resource_context"] = [{"kind": "file", "id": str(uuid4())}]
        case "remove_reference":
            del incoming["resource_context"]
        case unreachable:
            assert_never(unreachable)
    request = replace(case.request, input_payload=incoming)
    payload = await freeze_queued_resource_context_payload(db, case.scope, request)
    assert payload == public_input_payload(incoming)
    with pytest.raises(HTTPException) as conflict:
        await conversation_run_queue_service.enqueue_input_with_result(
            db,
            conversation_id=case.scope.conversation_id,
            context=case.scope.context,
            client_request_id=request.client_request_id,
            source="chat",
            input_payload=payload,
            attachment_ids=[],
            checkpoint_id=None,
            priority=0,
        )
    assert conflict.value.status_code == 409


async def test_new_authorized_queue_input_freezes_current_file_and_persists(
    db: AsyncSession, queue_resource_case: QueueResourceCase
) -> None:
    case = queue_resource_case
    request = replace(case.request, client_request_id="new-accepted-request")
    payload = await freeze_queued_resource_context_payload(db, case.scope, request)
    assert snapshot_bytes(payload) == case.snapshot
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=case.scope.conversation_id,
        context=case.scope.context,
        client_request_id=request.client_request_id,
        source="chat",
        input_payload=payload,
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    assert queued.id != case.queued.id
    assert snapshot_bytes(queued.input_payload) == case.snapshot
