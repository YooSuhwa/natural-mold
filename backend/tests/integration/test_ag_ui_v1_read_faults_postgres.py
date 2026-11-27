"""Open two authenticated SSE readers while PostgreSQL terminates journal writers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import RowMapping, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from starlette.types import Message, Receive, Scope, Send

from app.agent_runtime.event_broker import registry
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_protocol_event,
    to_protocol_wire_event,
)
from app.dependencies import get_db
from app.main import create_app
from app.models.conversation_run import ConversationRun, ConversationRunToolReceipt
from app.services import conversation_run_service, trace_storage
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.integration.test_chat_steer_concurrency import ActiveChat
from tests.integration.test_chat_steer_concurrency import active_chat as active_chat
from tests.integration.test_conversation_queue_concurrency import _postgres_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _append(
    db: AsyncSession, chat: ActiveChat, seq: int, content: str
) -> StoredProtocolEvent:
    event = stored_protocol_event(
        run_id=str(chat.run_id),
        thread_id=str(chat.conversation_id),
        seq=seq,
        method="messages",
        data={"type": "ai", "id": f"answer-{seq}", "content": content},
    )
    await trace_storage.append_events(
        db,
        conversation_id=chat.conversation_id,
        assistant_msg_id=str(chat.run_id),
        events_chunk=[dict(event)],
    )
    await db.flush()
    return event


@asynccontextmanager
async def _tab(chat: ActiveChat, stream_url: str) -> AsyncIterator[asyncio.Task[Response]]:
    """Observe an actual streaming response body before HTTPX buffers its final bytes."""
    app = create_app()

    async def isolated_db() -> AsyncIterator[AsyncSession]:
        async with chat.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    durable_prefix = asyncio.Event()

    async def observed_app(scope: Scope, receive: Receive, send: Send) -> None:
        async def observed_send(message: Message) -> None:
            if b"committed prefix" in message.get("body", b""):
                durable_prefix.set()
            await send(message)

        await app(scope, receive, observed_send)

    async with (
        AsyncClient(
            base_url="http://test",
            transport=ASGITransport(app=observed_app),
            cookies=chat.client.cookies,
        ) as client,
        asyncio.TaskGroup() as group,
    ):
        response = group.create_task(client.get(stream_url, params={"runId": str(chat.run_id)}))
        opened = group.create_task(durable_prefix.wait())
        try:
            await asyncio.wait([response, opened], return_when=asyncio.FIRST_COMPLETED)
            if response.done():
                result = response.result()
                pytest.fail(
                    f"SSE ended before submitting the terminal event ({result.status_code})"
                )
            yield response
        finally:
            opened.cancel()
            response.cancel()


async def _terminate_writer(chat: ActiveChat, writer: AsyncConnection, pid: int) -> None:
    # The caller holds this physical connection checked out, including after commit.
    # The controlling connection is a different session in the protected disposable lane.
    async with chat.sessions() as admin:
        assert await admin.scalar(text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": pid})
    await writer.invalidate()


async def _stored_state(chat: ActiveChat) -> RowMapping:
    async with chat.sessions() as db:
        return (
            (
                await db.execute(
                    text(
                        """
                    SELECT r.status, r.worker_instance_id, r.owner_generation, r.recovery_attempts,
                           r.last_event_id, r.heartbeat_at, t.status AS trace_status,
                           t.last_event_id AS trace_last_event, t.updated_at AS trace_updated,
                           (SELECT count(*) FROM message_event_chunks
                             WHERE message_event_id=t.id) AS chunks,
                           (SELECT count(*) FROM conversation_runs
                             WHERE conversation_id=r.conversation_id) AS runs,
                           (SELECT count(*) FROM conversation_run_inputs
                             WHERE conversation_id=r.conversation_id) AS inputs,
                           (SELECT count(*) FROM conversation_run_tool_receipts
                             WHERE run_id=r.id) AS effects,
                           (SELECT count(*) FROM audit_events
                             WHERE run_id=CAST(r.id AS text)) AS audit_count
                      FROM conversation_runs r JOIN message_events t
                        ON t.assistant_msg_id=CAST(r.id AS text)
                       AND t.conversation_id=r.conversation_id
                     WHERE r.id=:run_id
                    """
                    ),
                    {"run_id": chat.run_id},
                )
            )
            .mappings()
            .one()
        )


async def test_two_sse_readers_recover_commits_across_writer_termination(
    active_chat: ActiveChat, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = active_chat
    monkeypatch.setattr("app.services.conversation_stream_service.async_session", chat.sessions)
    prefix = f"/api/conversations/{chat.conversation_id}/ag-ui"
    params = {"runId": str(chat.run_id)}
    async with chat.sessions.begin() as db:
        await _append(db, chat, 1, "committed prefix")
        db.add(
            ConversationRunToolReceipt(
                run_id=chat.run_id,
                tenant_id=TEST_TENANT_ID,
                org_id=TEST_ORG_ID,
                tool_call_id="once",
                tool_name="fixture_write",
                arguments_sha256="0" * 64,
                effect_kind="external_write",
                status="completed",
                owner_generation=1,
                result_json={"ok": True},
            )
        )

    # A dedicated writer pool cannot accidentally terminate a reader after committing.
    _, writer_engine = _postgres_session_factory()
    try:
        async with (
            asyncio.timeout(30),
            _tab(chat, f"{prefix}/stream") as first,
            _tab(chat, f"{prefix}/stream") as second,
        ):
            before = await _stored_state(chat)
            page = await chat.client.get(f"{prefix}/events", params=params)
            assert page.status_code == 200
            assert page.json()["terminal"] is False
            assert "committed prefix" in page.text
            after = {**params, "after": page.json()["cursor"]}

            async with writer_engine.connect() as conn, AsyncSession(conn) as db:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                ghost = await _append(db, chat, 2, "uncommitted reply")
                broker = registry.get_or_create(
                    str(chat.run_id), conversation_id=str(chat.conversation_id)
                )
                broker.publish_nowait(
                    {
                        "id": ghost["id"],
                        "event": "message",
                        "data": dict(to_protocol_wire_event(ghost)),
                    }
                )
                assert broker.has_event_id(ghost["id"])
                pending = await chat.client.get(f"{prefix}/events", params=after)
                assert pending.status_code == 200
                assert pending.json()["frames"] == []
                await _terminate_writer(chat, conn, pid)

            broker.close()
            assert await _stored_state(chat) == before
            rolled_back = await chat.client.get(f"{prefix}/events", params=after)
            assert rolled_back.status_code == 200
            assert rolled_back.json()["frames"] == []

            async with writer_engine.connect() as conn, AsyncSession(conn) as db:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                final = await _append(db, chat, 2, "committed finish")
                end_id = final["id"]
                await trace_storage.finalize_turn(db, assistant_msg_id=str(chat.run_id))
                run = await db.scalar(
                    select(ConversationRun)
                    .where(ConversationRun.id == chat.run_id)
                    .with_for_update()
                )
                assert run is not None
                await conversation_run_service.transition_run(
                    db, run, "completed", last_event_id=end_id
                )
                await db.commit()
                await _terminate_writer(chat, conn, pid)

            finished = await _stored_state(chat)
            assert finished["status"] == finished["trace_status"] == "completed"
            assert finished["last_event_id"] == finished["trace_last_event"] == end_id
            assert finished["chunks"] == 2
            assert finished["runs"] == finished["effects"] == 1
            assert finished["inputs"] == 0
            for key in (
                "worker_instance_id",
                "owner_generation",
                "recovery_attempts",
                "heartbeat_at",
            ):
                assert finished[key] == before[key]
            live = await asyncio.gather(first, second)
            cold = await chat.client.get(f"{prefix}/stream", params=params)
            tail = await chat.client.get(f"{prefix}/events", params=after)
            assert tail.status_code == 200
            assert tail.json()["terminal"] is True
            assert "committed prefix" not in tail.text and "committed finish" in tail.text
            for response in [*live, cold]:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert "uncommitted reply" not in response.text
                frames = [
                    json.loads(line.removeprefix("data:"))
                    for line in response.text.splitlines()
                    if line.startswith("data:")
                ]
                types = [frame["type"] for frame in frames]
                assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
                assert types.count("RUN_STARTED") == types.count("RUN_FINISHED") == 1
                assert [f["delta"] for f in frames if f["type"] == "TEXT_MESSAGE_CONTENT"] == [
                    "committed prefix",
                    "committed finish",
                ]
            assert await _stored_state(chat) == finished
    finally:
        await writer_engine.dispose()
