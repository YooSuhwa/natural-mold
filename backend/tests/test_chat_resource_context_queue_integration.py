from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_worker, conversation_run_service
from app.services.chat_resource_context_payload import (
    INTERNAL_RESOURCE_CONTEXT_KEY,
    frozen_resource_context_from_payload,
)
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.test_chat_resource_context_support import (
    resource_context_run_start_body,
    seed_context_attachment,
)


async def _active_run(db: AsyncSession, conversation_id: uuid.UUID) -> ConversationRun:
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    active = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="active",
    )
    await conversation_run_service.transition_run(
        db, active, "running", worker_instance_id="remote-worker"
    )
    return active


@pytest.mark.asyncio
async def test_interrupt_retry_does_not_repeat_cancel_or_refresh_context(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    await _active_run(db, conversation_id)
    attachment = await seed_context_attachment(db, conversation_id, tmp_path)
    await db.commit()
    endpoint = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    body = resource_context_run_start_body(attachment.id, "interrupt-context-retry")
    body["params"]["multitask_strategy"] = "interrupt"

    first = await client.post(endpoint, json=body)
    await run_in_threadpool(
        Path(attachment.storage_path).write_text,
        "changed before retry",
        encoding="utf-8",
    )
    second = await client.post(endpoint, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["result"]["input_id"] == first.json()["result"]["input_id"]
    queued = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.client_request_id == "interrupt-context-retry"
        )
    )
    assert queued is not None
    frozen = frozen_resource_context_from_payload(queued.input_payload)
    assert frozen is not None
    assert frozen.resources[0].text == "enqueue snapshot"
    steer_audits = (
        await db.scalars(
            select(AuditEvent).where(AuditEvent.action == "conversation.run_steer_request")
        )
    ).all()
    assert len(steer_audits) == 1


@pytest.mark.asyncio
async def test_dispatch_fails_honestly_when_context_access_is_revoked(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    active = await _active_run(db, conversation_id)
    attachment = await seed_context_attachment(db, conversation_id, tmp_path)
    await db.commit()
    endpoint = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"

    accepted = await client.post(
        endpoint, json=resource_context_run_start_body(attachment.id, "revoked-context")
    )
    assert accepted.status_code == 200
    await db.delete(attachment)
    await conversation_run_service.transition_run(db, active, "completed")
    await db.commit()

    await conversation_run_queue_worker.dispatch_next_for_conversation(conversation_id)

    queued = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.client_request_id == "revoked-context"
        )
    )
    assert queued is not None
    await db.refresh(queued)
    assert queued.status == "failed"
    assert queued.run_id is not None
    failed_run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=queued.run_id,
        user_id=TEST_USER_ID,
    )
    assert failed_run is not None
    assert failed_run.error_code == "queue_resource_context_unavailable"


@pytest.mark.asyncio
async def test_queue_edit_refreezes_refs_and_never_accepts_internal_snapshot(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    await _active_run(db, conversation_id)
    attachment = await seed_context_attachment(db, conversation_id, tmp_path)
    await db.commit()
    command_endpoint = (
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    )
    accepted = await client.post(
        command_endpoint,
        json=resource_context_run_start_body(attachment.id, "editable-context"),
    )
    input_id = accepted.json()["result"]["input_id"]
    await run_in_threadpool(
        Path(attachment.storage_path).write_text,
        "edited snapshot",
        encoding="utf-8",
    )
    edit_endpoint = f"/api/conversations/{conversation_id}/run-inputs/{input_id}"

    edited = await client.patch(
        edit_endpoint,
        json={
            "expected_revision": 1,
            "input": {
                "messages": [{"role": "user", "content": "edited question"}],
                "resource_context": [{"kind": "file", "id": str(attachment.id)}],
            },
        },
    )
    forged = await client.patch(
        edit_endpoint,
        json={
            "expected_revision": 2,
            "input": {INTERNAL_RESOURCE_CONTEXT_KEY: {"resources": []}},
        },
    )

    assert edited.status_code == 200
    assert edited.json()["resource_context"] == [{"kind": "file", "id": str(attachment.id)}]
    assert INTERNAL_RESOURCE_CONTEXT_KEY not in edited.json()["input_payload"]
    assert forged.status_code == 422
    queued = await db.get(ConversationRunInput, uuid.UUID(input_id))
    assert queued is not None
    frozen = frozen_resource_context_from_payload(queued.input_payload)
    assert frozen is not None
    assert frozen.resources[0].text == "edited snapshot"
    assert queued.revision == 2
