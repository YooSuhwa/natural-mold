"""W3-out M2 router-level smoke — ``X-Run-Id`` response header contract.

Streaming-layer dual-write (broker.publish + trace_sink + persist_callback)
is covered exhaustively in ``tests/test_streaming.py`` (see the
``W3-out M2 — broker dual-write`` block: 4 tests). DB-side append/dedup is
covered in ``tests/test_trace_storage_partial.py``.

This file owns the **router contract**: POST ``/messages`` must surface a
``X-Run-Id`` header so the frontend (M5) can hold it and later GET-resume
(M3). Without that header the entire resume feature is unreachable, so it's
the cheapest end-to-end signal we can guard at S5 time.

End-to-end "POST → DB row + broker live" verification depends on the M3 GET
endpoint and is deferred to M6 integration tests (see CHECKPOINT.md). The
on_complete callback opens a fresh ``async_session()`` against the real
configured DB which the in-memory aiosqlite test harness does not back —
spinning up that fixture is itself M6 work.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


async def _seed() -> uuid.UUID:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=user.id,
            name="DualWrite Agent",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="DualWrite Conv")
        db.add(conv)
        await db.commit()
        return conv.id


@pytest.mark.asyncio
async def test_send_message_exposes_x_run_id_header(client: AsyncClient) -> None:
    """Router must surface ``X-Run-Id`` so the frontend can later GET-resume."""
    conv_id = await _seed()

    async def mock_stream(*args: Any, **kwargs: Any):
        run_id = kwargs.get("run_id") or "fallback-run-id"
        yield (
            f'event: message_start\nid: {run_id}-1\n'
            f'data: {{"id": "{run_id}", "role": "assistant"}}\n\n'
        )
        yield (
            f'event: message_end\nid: {run_id}-2\n'
            f'data: {{"content": "ok", "usage": {{}}}}\n\n'
        )

    with patch(
        "app.routers.conversations.execute_agent_stream", side_effect=mock_stream
    ):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "say hi"},
        )

    assert resp.status_code == 200
    run_id = resp.headers.get("X-Run-Id")
    assert run_id, "X-Run-Id header missing — frontend cannot GET-resume"
    # Must be a valid UUID string (router uses uuid.uuid4 — see
    # conversations.py ``send_message``).
    uuid.UUID(run_id)


@pytest.mark.asyncio
async def test_send_message_registers_broker_for_run_id(
    client: AsyncClient,
) -> None:
    """Router must register a broker in ``BrokerRegistry`` for that run_id
    *before* the executor is invoked, so a parallel GET-resume in M3 can
    discover it. We assert from inside the mock executor (= live phase)."""
    from app.agent_runtime import event_broker

    conv_id = await _seed()
    seen: dict[str, Any] = {}

    async def mock_stream(*args: Any, **kwargs: Any):
        run_id = kwargs.get("run_id") or ""
        seen["run_id"] = run_id
        seen["broker_live"] = event_broker.registry.get(run_id)
        yield (
            f'event: message_start\nid: {run_id}-1\n'
            f'data: {{"id": "{run_id}", "role": "assistant"}}\n\n'
        )
        yield (
            f'event: message_end\nid: {run_id}-2\n'
            f'data: {{"content": "", "usage": {{}}}}\n\n'
        )

    with patch(
        "app.routers.conversations.execute_agent_stream", side_effect=mock_stream
    ):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "hello"},
        )

    assert resp.status_code == 200
    assert seen.get("run_id"), "executor was not invoked"
    assert seen.get("broker_live") is not None, (
        "broker not registered before executor — GET resume in M3 cannot attach"
    )
