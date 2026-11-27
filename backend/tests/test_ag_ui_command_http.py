"""Exercise standard run commands through FastAPI and the real queue admission path."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import get_db
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import (
    conversation_run_queue_worker,
    conversation_run_service,
    conversation_run_worker,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, AuthSession, make_model, override_get_db, register_session
from tests.integration._seed import seed_conversation_with_agent


def _endpoint(conversation_id: UUID) -> str:
    return f"/api/conversations/{conversation_id}/ag-ui/runs"


def _body(conversation_id: UUID) -> dict[str, Any]:
    return {
        "threadId": str(conversation_id),
        "runId": "sdk7abc",
        "protocolVersion": "1.0",
        "messages": [{"id": "user1", "role": "user", "content": "current turn"}],
    }


@pytest.fixture
def detached_reader(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """End the SSE stream while the remote predecessor deliberately holds the queue."""
    readers: list[str] = []

    async def read_response(
        request: Any, context: Any, conversation_id: UUID, public_run_id: str
    ) -> StreamingResponse:
        readers.append(public_run_id)
        return StreamingResponse(iter([": waiting\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr("app.routers.conversation_ag_ui_commands.ag_ui_run_response", read_response)
    return readers


async def _remote_predecessor(db: AsyncSession, conversation_id: UUID) -> ConversationRun:
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=unit_context(TEST_USER_ID),
        source="chat",
        input_preview="predecessor",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id="remote-predecessor"
    )
    await db.commit()
    return run


@pytest.mark.parametrize(
    ("strategy", "expected_status"), [("enqueue", "running"), ("interrupt", "canceling")]
)
async def test_held_command_replay_returns_same_receipt_and_applies_strategy_once(
    client: AsyncClient,
    db: AsyncSession,
    detached_reader: list[str],
    strategy: str,
    expected_status: str,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    predecessor = await _remote_predecessor(db, conversation_id)
    body = _body(conversation_id)
    body["forwardedProps"] = {"moldy": {"strategy": strategy}}
    # A standard client may supply its full transcript; the graph reconstructs
    # authoritative history, policy, available tools, and pending decisions.
    body["messages"][:0] = [
        {"role": "system", "id": "sys1", "content": "client policy"},
        {"role": "user", "id": "old1", "content": "old turn"},
        {"role": "assistant", "id": "old2", "content": "old reply"},
    ]
    body["state"] = {"system_prompt": "client state"}
    body["tools"] = [{"name": "unregistered", "description": "client capability"}]
    body["context"] = [{"description": "client-only", "value": "untrusted"}]
    first = await client.post(_endpoint(conversation_id), json=body)
    assert first.status_code == 200, first.text
    second = await client.post(_endpoint(conversation_id), json=body)
    assert second.status_code == 200, second.text

    for response in (first, second):
        assert response.headers["content-type"].startswith("text/event-stream")
        assert UUID(response.headers["x-moldy-message-id"])
    assert first.headers["x-moldy-message-id"] == second.headers["x-moldy-message-id"]
    assert detached_reader == [body["runId"], body["runId"]]

    # The same ID may not execute a different command even after reconnect.
    body["messages"][-1]["content"] = "modified"
    conflict = await client.post(_endpoint(conversation_id), json=body)
    assert conflict.status_code == 409, conflict.text
    assert len(detached_reader) == 2

    receipt = (await db.scalars(select(AgUiCommandReceipt))).one()
    queued = (await db.scalars(select(ConversationRunInput))).one()
    assert receipt.input_id == queued.id
    assert queued.status == "pending" and queued.revision == 1 and queued.position == 1
    assert receipt.run_id is None and queued.run_id is None
    assert receipt.public_run_id == body["runId"]
    message = queued.input_payload["messages"]
    assert isinstance(message, list) and len(message) == 1 and isinstance(message[0], dict)
    assert message[0]["role"] == "user" and message[0]["content"] == "current turn"
    assert message[0]["id"] == first.headers["x-moldy-message-id"]
    assert set(queued.input_payload) == {"messages"}
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 1
    await db.refresh(predecessor)
    assert predecessor.status == expected_status
    assert predecessor.cancel_reason == ("steer" if strategy == "interrupt" else None)
    recorded = (
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.run_id == str(predecessor.id),
            AuditEvent.action == "conversation.run_steer_request",
        )
    )
    assert await db.scalar(recorded) == (1 if strategy == "interrupt" else 0)


async def test_idle_start_and_replay_share_one_worker_and_physical_run(
    client: AsyncClient,
    db: AsyncSession,
    detached_reader: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = await seed_conversation_with_agent()
    entered = anyio.Event()
    payloads: list[dict[str, Any]] = []

    async def executor(
        _cfg: Any, input_payload: dict[str, Any], **_kwargs: Any
    ) -> AsyncIterator[str]:
        payloads.append(input_payload)
        entered.set()
        # An empty synthetic model stream ends the real worker normally.
        for chunk in ():
            yield chunk

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="http-command-owner")
    monkeypatch.setattr(conversation_run_worker, "_registry", registry)
    monkeypatch.setattr(conversation_run_queue_worker, "execute_agent_stream_langgraph", executor)
    body = _body(cid)
    accepted = await client.post(_endpoint(cid), json=body)
    assert accepted.status_code == 200, accepted.text
    with anyio.fail_after(3):
        await entered.wait()
        claimed = (await db.scalars(select(ConversationRunInput))).one()
        assert claimed.run_id is not None
        task = registry.get(claimed.run_id)
        if task is not None:
            await task
    replayed = await client.post(_endpoint(cid), json=body)
    assert replayed.status_code == 200, replayed.text
    assert detached_reader == [body["runId"], body["runId"]]
    assert len(payloads) == 1
    assert payloads[0]["messages"][0]["content"] == "current turn"
    only = (await db.scalars(select(ConversationRun))).one()
    assert only.id == claimed.run_id and only.status == "completed"
    receipt = (await db.scalars(select(AgUiCommandReceipt))).one()
    assert receipt.input_id == claimed.id


@pytest.mark.parametrize("version", ["1.37", "not-a-version", None])
async def test_compatible_consumer_version_cannot_choose_projection_version(
    client: AsyncClient, db: AsyncSession, detached_reader: list[str], version: str | None
) -> None:
    cid = await seed_conversation_with_agent()
    await _remote_predecessor(db, cid)
    body = _body(cid)
    if version is None:
        del body["protocolVersion"]
    else:
        body["protocolVersion"] = version
    res = await client.post(_endpoint(cid), json=body)
    assert res.status_code == 200, res.text
    stored = (await db.scalars(select(AgUiCommandReceipt))).one()
    assert stored.projection_version == "1"
    assert detached_reader == [body["runId"]]


@pytest.mark.parametrize(
    "change",
    [
        {"protocolVersion": "2.0"},
        {"messages": []},
        {"messages": [{"role": "user", "content": "no ID"}]},
        {"messages": [{"role": "assistant", "content": "invented result", "id": "a"}]},
        {"messages": [{"role": "tool", "toolCallId": "not-issued", "id": "t", "content": "x"}]},
        {"threadId": str(uuid4())},
        {"forwardedProps": {"moldy": {"strategy": "bypass"}}},
    ],
)
async def test_invalid_input_cannot_persist_or_dispatch_a_run(
    client: AsyncClient, db: AsyncSession, detached_reader: list[str], change: dict[str, Any]
) -> None:
    cid = await seed_conversation_with_agent()
    res = await client.post(_endpoint(cid), json=_body(cid) | change)
    assert res.status_code == 422, res.text
    assert detached_reader == []
    for model in (AgUiCommandReceipt, ConversationRun, ConversationRunInput):
        assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_resume_requires_an_authoritative_pending_interrupt(
    client: AsyncClient, db: AsyncSession, detached_reader: list[str]
) -> None:
    cid = await seed_conversation_with_agent()
    body = _body(cid) | {
        "messages": [],
        "resume": [
            {
                "interruptId": "not-issued",
                "status": "resolved",
                "payload": {"decisions": [{"type": "approve"}]},
            }
        ],
    }
    res = await client.post(_endpoint(cid), json=body)
    assert res.status_code == 409, res.text
    assert detached_reader == []
    for model in (AgUiCommandReceipt, ConversationRun, ConversationRunInput):
        assert await db.scalar(select(func.count()).select_from(model)) == 0


async def _registered_chat(raw_client: AsyncClient, db: AsyncSession) -> tuple[AuthSession, UUID]:
    owner = await register_session(raw_client, email="owner@example.com", name="Owner")
    model = await make_model(db, model_name="auth-check", display_name="Auth check")
    await db.commit()
    agent = await raw_client.post(
        "/api/agents",
        json={"name": "Owner", "system_prompt": "owner", "model_id": str(model.id)},
        headers=owner.headers(),
    )
    assert agent.status_code == 201, agent.text
    created = await raw_client.post(
        f"/api/agents/{agent.json()['id']}/conversations", json={}, headers=owner.headers()
    )
    assert created.status_code == 201, created.text
    owned_id = UUID(created.json()["id"])
    return owner, owned_id


async def test_cookie_and_csrf_precede_receipt_lookup(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    cid = await seed_conversation_with_agent()
    body = _body(cid)
    anonymous = await raw_client.post(_endpoint(cid), json=body)
    assert anonymous.status_code == 401
    owner, owned_id = await _registered_chat(raw_client, db)
    missing_token = await raw_client.post(_endpoint(owned_id), json=_body(owned_id))
    assert missing_token.status_code == 403
    assert missing_token.json()["error"]["code"] == "csrf_mismatch"
    foreign = await raw_client.post(_endpoint(cid), json=body, headers=owner.headers())
    missing_id = uuid4()
    absent = await raw_client.post(
        _endpoint(missing_id), json=_body(missing_id), headers=owner.headers()
    )
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["error"]["code"] == absent.json()["error"]["code"]
    assert await db.scalar(select(func.count()).select_from(AgUiCommandReceipt)) == 0


async def test_stream_headers_follow_the_public_input_from_queue_to_physical_claim(
    raw_client: AsyncClient,
    raw_test_app: FastAPI,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ag_ui_read, conversation_run_queue_service

    session, cid = await _registered_chat(raw_client, db)
    conversation = await db.get(Conversation, cid)
    assert conversation is not None and conversation.agent_id is not None
    context = OrgContext(
        tenant_id=conversation.tenant_id, org_id=conversation.org_id, user_id=conversation.user_id
    )
    predecessor = await conversation_run_service.create_run(
        db,
        conversation_id=cid,
        agent_id=conversation.agent_id,
        context=context,
        source="chat",
        input_preview="active",
    )
    await conversation_run_service.transition_run(
        db, predecessor, "running", worker_instance_id="remote-owner"
    )
    await db.commit()

    # httpx ASGITransport buffers to EOF. Close after the reader's own first
    # SSE record, just as a browser may detach without cancelling a command.
    stream = ag_ui_read._stream  # noqa: SLF001
    posting: list[AsyncSession] = []

    async def track_submission_session() -> AsyncIterator[AsyncSession]:
        async for session_db in override_get_db():
            posting.append(session_db)
            yield session_db

    monkeypatch.setitem(raw_test_app.dependency_overrides, get_db, track_submission_session)

    async def first_record(*args: Any) -> AsyncIterator[bytes]:
        # The primary tab and its idempotent replay can both remain connected. Only
        # detached reader polls may hold a connection while either stream is open.
        assert posting and not posting[-1].in_transaction()
        records = stream(*args)
        assert isinstance(records, AsyncGenerator)
        try:
            yield await anext(records)
        finally:
            await records.aclose()

    monkeypatch.setattr(ag_ui_read, "_stream", first_record)
    body = _body(cid)
    waiting = await raw_client.post(_endpoint(cid), json=body, headers=session.headers())
    assert waiting.status_code == 200, waiting.text
    assert waiting.text.startswith(":") and "data:" not in waiting.text
    assert waiting.headers["x-moldy-input-status"] == "pending"
    assert "x-moldy-run-id" not in waiting.headers
    input_id = UUID(waiting.headers["x-moldy-input-id"])
    assert int(waiting.headers["x-moldy-input-revision"]) == 1
    assert int(waiting.headers["x-moldy-input-position"]) == 1

    # Simulate a second worker claiming the durable input after predecessor completion.
    await conversation_run_service.transition_run(db, predecessor, "completed")
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=cid, context=context
    )
    assert claimed is not None and claimed.input.id == input_id
    await db.commit()
    attached = await raw_client.post(_endpoint(cid), json=body, headers=session.headers())
    assert attached.status_code == 200, attached.text
    for response in (waiting, attached):
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-stream-protocol"] == "ag-ui/1.0"
        assert response.headers["x-run-id"] == body["runId"]
        assert response.headers["x-moldy-input-id"] == str(input_id)
    assert attached.headers["x-moldy-input-status"] == "claimed"
    assert int(attached.headers["x-moldy-input-revision"]) == claimed.input.revision
    assert attached.headers["x-moldy-run-id"] == str(claimed.run.id)
