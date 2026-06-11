from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.agent_runtime import event_names
from app.agent_runtime.streaming import format_sse
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.services import trace_storage
from app.services.conversation_run_worker import RunTaskRegistry, reset_run_task_registry_for_tests
from tests.conftest import TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.fixture
def patch_runtime_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.conversation_stream_service.async_session", TestSession)
    monkeypatch.setattr("app.services.conversation_run_worker.async_session", TestSession)


def _events(run_id: str, *, content: str = "ok") -> list[dict[str, Any]]:
    return [
        {
            "id": f"{run_id}-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": run_id, "role": "assistant"},
        },
        {
            "id": f"{run_id}-2",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": content},
        },
        {
            "id": f"{run_id}-3",
            "event": event_names.MESSAGE_END,
            "data": {"content": content, "usage": {}},
        },
    ]


async def _publish_event(kwargs: dict[str, Any], evt: dict[str, Any]) -> str:
    kwargs["broker"].publish_nowait(evt)
    kwargs["trace_sink"].append(evt)
    await kwargs["persist_callback"]([evt])
    return format_sse(evt["event"], evt["data"], event_id=evt["id"])


async def _wait_for_run_status(
    run_id: str,
    status: str,
    *,
    timeout_seconds: float = 2.0,
) -> ConversationRun:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_seen: str | None = None
    while loop.time() < deadline:
        async with TestSession() as db:
            run = await db.get(ConversationRun, uuid.UUID(run_id))
            if run is not None:
                last_seen = run.status
                if run.status == status:
                    return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"timeout waiting for run {run_id} status={status}; last={last_seen}")


async def _audit_actions_for_run(run_id: str) -> set[str]:
    async with TestSession() as db:
        rows = (
            (await db.execute(select(AuditEvent).where(AuditEvent.run_id == run_id)))
            .scalars()
            .all()
        )
    return {row.action for row in rows}


@pytest.mark.asyncio
async def test_post_message_creates_durable_run_and_finalizes_completed(
    client: AsyncClient,
    patch_runtime_sessions: None,
) -> None:
    conversation_id = await seed_conversation_with_agent()

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        for evt in _events(kwargs["run_id"]):
            yield await _publish_event(kwargs, evt)

    with patch("app.routers.conversation_messages.execute_agent_stream", side_effect=mock_stream):
        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "hello durable run"},
        )

    assert resp.status_code == 200
    run_id = resp.headers["x-run-id"]
    assert uuid.UUID(run_id)
    assert f'"id":"{run_id}"' in resp.text

    run = await _wait_for_run_status(run_id, "completed")
    assert run.conversation_id == conversation_id
    assert run.source == "chat"
    assert run.is_active is False
    assert run.last_event_id == f"{run_id}-3"

    async with TestSession() as db:
        record = (
            await db.execute(select(MessageEvent).where(MessageEvent.assistant_msg_id == run_id))
        ).scalar_one()
        assert record.status == "completed"
    assert await _audit_actions_for_run(run_id) >= {
        "conversation.run_start",
        "conversation.run_complete",
    }


@pytest.mark.asyncio
async def test_worker_continues_after_initial_stream_disconnect(
    client: AsyncClient,
    patch_runtime_sessions: None,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    first_event_published = asyncio.Event()
    release_worker = asyncio.Event()
    captured: dict[str, str] = {}

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        run_id = kwargs["run_id"]
        captured["run_id"] = run_id
        events = _events(run_id, content="detached")
        yield await _publish_event(kwargs, events[0])
        first_event_published.set()
        await release_worker.wait()
        for evt in events[1:]:
            yield await _publish_event(kwargs, evt)

    with patch("app.routers.conversation_messages.execute_agent_stream", side_effect=mock_stream):
        post_task = asyncio.create_task(
            client.post(
                f"/api/conversations/{conversation_id}/messages",
                json={"content": "disconnect after first event"},
            )
        )
        await first_event_published.wait()
        run_id = captured["run_id"]
        post_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await post_task
        release_worker.set()
        run = await _wait_for_run_status(run_id, "completed")

    assert run.is_active is False
    assert run.last_event_id == f"{run_id}-3"

    # Keep any failed worker task from leaking into later tests if the assertion
    # above times out while the mock is still paused.
    if not release_worker.is_set():
        release_worker.set()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_worker_shutdown_marks_owned_active_run_stale(
    client: AsyncClient,
    patch_runtime_sessions: None,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    registry = RunTaskRegistry(worker_instance_id="shutdown-test-worker")
    reset_run_task_registry_for_tests(registry)
    started = asyncio.Event()
    captured: dict[str, str] = {}

    async def blocking_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        captured["run_id"] = kwargs["run_id"]
        started.set()
        await asyncio.Event().wait()
        if False:  # pragma: no cover - keeps this an async generator
            yield ""

    try:
        with patch(
            "app.routers.conversation_messages.execute_agent_stream",
            side_effect=blocking_stream,
        ):
            post_task = asyncio.create_task(
                client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "shutdown while running"},
                )
            )

            await started.wait()
            run_id = captured["run_id"]

            await registry.shutdown(timeout_seconds=0.5)
            resp = await post_task
            assert resp.status_code == 200

        run = await _wait_for_run_status(run_id, "stale")
        assert run.worker_instance_id == registry.worker_instance_id
        assert run.is_active is False
        assert run.error_code == "worker_shutdown"
        assert "conversation.run_stale" in await _audit_actions_for_run(run_id)
    finally:
        reset_run_task_registry_for_tests()


@pytest.mark.asyncio
async def test_cancel_endpoint_cancels_running_worker_and_closes_replay_log(
    client: AsyncClient,
    patch_runtime_sessions: None,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    registry = RunTaskRegistry(worker_instance_id="cancel-test-worker")
    reset_run_task_registry_for_tests(registry)
    started = asyncio.Event()
    captured: dict[str, str] = {}

    async def blocking_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        run_id = kwargs["run_id"]
        captured["run_id"] = run_id
        yield await _publish_event(
            kwargs,
            {
                "id": f"{run_id}-1",
                "event": event_names.MESSAGE_START,
                "data": {"id": run_id, "role": "assistant"},
            },
        )
        yield await _publish_event(
            kwargs,
            {
                "id": f"{run_id}-2",
                "event": event_names.CONTENT_DELTA,
                "data": {"delta": "partial before cancel"},
            },
        )
        started.set()
        await asyncio.Event().wait()
        if False:  # pragma: no cover - keeps this an async generator
            yield ""

    try:
        with patch(
            "app.routers.conversation_messages.execute_agent_stream",
            side_effect=blocking_stream,
        ):
            post_task = asyncio.create_task(
                client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "cancel this run"},
                )
            )
            await started.wait()
            run_id = captured["run_id"]

            cancel_resp = await client.post(
                f"/api/conversations/{conversation_id}/runs/{run_id}/cancel"
            )
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["status"] == "canceling"

            post_resp = await post_task
            assert post_resp.status_code == 200

        run = await _wait_for_run_status(run_id, "canceled")
        assert run.is_active is False
        assert run.cancel_requested_at is not None

        async with TestSession() as db:
            record = (
                await db.execute(
                    select(MessageEvent).where(MessageEvent.assistant_msg_id == run_id)
                )
            ).scalar_one()
            assert record.status == "completed"
            events = await trace_storage.load_events(db, record)
            assert events[-1]["event"] == event_names.MESSAGE_END
            assert events[-1]["data"]["status"] == "canceled"
        assert await _audit_actions_for_run(run_id) >= {
            "conversation.run_start",
            "conversation.run_cancel_request",
            "conversation.run_canceled",
        }
    finally:
        await registry.shutdown(timeout_seconds=0.5)
        reset_run_task_registry_for_tests()


@pytest.mark.asyncio
async def test_interrupt_event_finalizes_run_as_interrupted(
    client: AsyncClient,
    patch_runtime_sessions: None,
) -> None:
    conversation_id = await seed_conversation_with_agent()

    async def interrupting_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        run_id = kwargs["run_id"]
        events = [
            {
                "id": f"{run_id}-1",
                "event": event_names.MESSAGE_START,
                "data": {"id": run_id, "role": "assistant"},
            },
            {
                "id": f"{run_id}-2",
                "event": event_names.INTERRUPT,
                "data": {
                    "interrupt_id": "approval:1",
                    "action_requests": [],
                    "review_configs": [],
                },
            },
            {
                "id": f"{run_id}-3",
                "event": event_names.MESSAGE_END,
                "data": {"content": "", "usage": {}, "status": "completed"},
            },
        ]
        for evt in events:
            yield await _publish_event(kwargs, evt)

    with patch(
        "app.routers.conversation_messages.execute_agent_stream",
        side_effect=interrupting_stream,
    ):
        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "needs approval"},
        )

    assert resp.status_code == 200
    run_id = resp.headers["x-run-id"]

    run = await _wait_for_run_status(run_id, "interrupted")
    assert run.is_active is False
    assert run.interrupt_id == "approval:1"

    async with TestSession() as db:
        record = (
            await db.execute(select(MessageEvent).where(MessageEvent.assistant_msg_id == run_id))
        ).scalar_one()
        assert record.status == "completed"
    assert "conversation.run_interrupted" in await _audit_actions_for_run(run_id)
