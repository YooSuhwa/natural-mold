from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_worker
from app.services.chat_resource_context_payload import (
    INTERNAL_RESOURCE_CONTEXT_KEY,
    frozen_resource_context_from_payload,
)
from tests.integration._seed import seed_conversation_with_agent
from tests.test_chat_resource_context_support import (
    resource_context_run_start_body,
    seed_context_attachment,
)


@dataclass(frozen=True, slots=True)
class DispatchCapture:
    storage_root: Path
    payloads: list[dict[str, Any]]


@pytest.fixture
def dispatch_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DispatchCapture:
    payloads: list[dict[str, Any]] = []

    async def capture_start(**kwargs: Any) -> None:
        payloads.append(kwargs["input_payload"])

    monkeypatch.setattr(conversation_run_queue_worker, "start_conversation_run", capture_start)
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        capture_start,
    )
    return DispatchCapture(storage_root=tmp_path, payloads=payloads)


@pytest.mark.asyncio
async def test_run_start_persists_dispatches_and_replays_frozen_context(
    client: AsyncClient,
    db: AsyncSession,
    dispatch_capture: DispatchCapture,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    attachment = await seed_context_attachment(db, conversation_id, dispatch_capture.storage_root)
    await db.commit()
    endpoint = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    body = resource_context_run_start_body(attachment.id, "context-idempotent")

    first = await client.post(endpoint, json=body)
    assert first.status_code == 200
    queued = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.client_request_id == "context-idempotent"
        )
    )
    assert queued is not None
    frozen = frozen_resource_context_from_payload(queued.input_payload)
    assert frozen is not None
    assert frozen.resources[0].text == "enqueue snapshot"
    assert len(dispatch_capture.payloads) == 1
    assert INTERNAL_RESOURCE_CONTEXT_KEY not in dispatch_capture.payloads[0]
    assert dispatch_capture.payloads[0]["messages"][0]["role"] == "user"

    await run_in_threadpool(
        Path(attachment.storage_path).write_text,
        "changed after acceptance",
        encoding="utf-8",
    )
    retried = await client.post(endpoint, json=body)
    assert retried.status_code == 200
    await db.refresh(queued)
    replayed = frozen_resource_context_from_payload(queued.input_payload)
    assert replayed is not None
    assert replayed.resources[0].text == "enqueue snapshot"

    listed = await client.get(f"/api/conversations/{conversation_id}/run-inputs")
    item = listed.json()["items"][0]
    assert item["resource_context"] == [
        {"kind": "file", "id": str(attachment.id), "label": "shown label"}
    ]
    assert INTERNAL_RESOURCE_CONTEXT_KEY not in item["input_payload"]
    assert "enqueue snapshot" not in listed.text


@pytest.mark.asyncio
async def test_direct_run_start_injects_context_only_as_user_input(
    client: AsyncClient,
    db: AsyncSession,
    dispatch_capture: DispatchCapture,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    attachment = await seed_context_attachment(db, conversation_id, dispatch_capture.storage_root)
    await db.commit()
    endpoint = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    body = resource_context_run_start_body(attachment.id, "direct-context")
    body["params"]["multitask_strategy"] = "reject"

    response = await client.post(endpoint, json=body)

    assert response.status_code == 200
    assert len(dispatch_capture.payloads) == 1
    runtime_payload = dispatch_capture.payloads[0]
    assert INTERNAL_RESOURCE_CONTEXT_KEY not in runtime_payload
    assert runtime_payload["messages"][0]["role"] == "user"
    assert runtime_payload["messages"][1]["content"] == "use context"
    run_id = uuid.UUID(response.json()["result"]["run_id"])
    persisted = await db.scalar(
        select(ConversationRunInput).where(ConversationRunInput.run_id == run_id)
    )
    assert persisted is not None
    frozen = frozen_resource_context_from_payload(persisted.input_payload)
    assert frozen is not None
    assert frozen.resources[0].text == "enqueue snapshot"
