"""W3-out M3 — GET /api/conversations/{id}/stream resume endpoint.

4 분기 검증 (plan 파일 ``M3 — GET resume endpoint``):
- 시나리오 A: live attach (broker hit) → ``X-Resume-Mode: live`` 로 broker
  buffer + 후속 publish 가 stream 으로 전달
- 시나리오 B: replay only (broker miss + DB row completed) → ``X-Resume-Mode:
  replay`` 로 events 슬라이스 만 emit
- 시나리오 C: stale streaming (broker miss + DB row status='streaming') →
  events emit 후 ``event: stale`` 마커 발행
- 시나리오 D: HiTL interrupt pending → ``409 RESUME_INTERRUPT_PENDING``

추가 가드:
- run_id 미존재 → ``404 RESUME_NOT_FOUND``
- run_id 가 다른 conversation 소속 → ``403 RESUME_FORBIDDEN``
- ``Last-Event-ID`` 헤더 fallback (query 우선)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.agent_runtime import event_broker
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conv() -> uuid.UUID:
    async with TestSession() as db:
        # User row may already exist from autouse fixture? No, conftest just
        # creates the schema — seed our own.
        existing = await db.get(User, TEST_USER_ID)
        if existing is None:
            db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=TEST_USER_ID,
            name="Resume Agent",
            system_prompt="x",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="Resume Conv")
        db.add(conv)
        await db.commit()
        return conv.id


def _parse_sse_events(body: str) -> list[dict[str, str]]:
    """Split a multi-event SSE payload into ``{event, id?, data}`` dicts."""
    events: list[dict[str, str]] = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        evt: dict[str, str] = {}
        for line in chunk.splitlines():
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            evt[field.strip()] = value.lstrip()
        if evt:
            events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Scenario A — live broker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_live_broker_replays_buffer_and_streams_tail(
    client: AsyncClient,
) -> None:
    """broker live → buffered events 전달 + close 시 자연 종료."""
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())

    broker = event_broker.registry.get_or_create(
        run_id, conversation_id=str(conv_id)
    )
    # Pre-publish two events so the GET request can replay them on subscribe.
    broker.publish_nowait(
        {"id": f"{run_id}-1", "event": "message_start",
         "data": {"id": run_id, "role": "assistant"}}
    )
    broker.publish_nowait(
        {"id": f"{run_id}-2", "event": "content_delta",
         "data": {"delta": "hello"}}
    )

    async def push_tail() -> None:
        # Let the GET request register its listener before we publish more.
        await asyncio.sleep(0.05)
        broker.publish_nowait(
            {"id": f"{run_id}-3", "event": "content_delta",
             "data": {"delta": " world"}}
        )
        await asyncio.sleep(0.01)
        broker.publish_nowait(
            {"id": f"{run_id}-4", "event": "message_end",
             "data": {"content": "hello world", "usage": {}}}
        )
        # Close so the subscribe iterator exits.
        broker.close()

    pusher = asyncio.create_task(push_tail())
    try:
        async with client.stream(
            "GET",
            f"/api/conversations/{conv_id}/stream",
            params={"run_id": run_id},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["x-run-id"] == run_id
            assert resp.headers["x-resume-mode"] == "live"
            body = await resp.aread()
    finally:
        await pusher

    events = _parse_sse_events(body.decode())
    deltas = [
        json.loads(e["data"])["delta"]
        for e in events
        if e.get("event") == "content_delta"
    ]
    assert deltas == ["hello", " world"]
    assert any(e.get("event") == "message_end" for e in events)


@pytest.mark.asyncio
async def test_resume_live_broker_after_id_skips_already_seen(
    client: AsyncClient,
) -> None:
    """``last_event_id`` 이후 이벤트만 replay."""
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())

    broker = event_broker.registry.get_or_create(
        run_id, conversation_id=str(conv_id)
    )
    broker.publish_nowait(
        {"id": f"{run_id}-1", "event": "message_start",
         "data": {"id": run_id}}
    )
    broker.publish_nowait(
        {"id": f"{run_id}-2", "event": "content_delta", "data": {"delta": "a"}}
    )
    broker.publish_nowait(
        {"id": f"{run_id}-3", "event": "content_delta", "data": {"delta": "b"}}
    )

    # Close after the GET subscribes — closing first would route the request
    # to the DB-replay branch (broker.is_closed → broker miss).
    async def close_after_subscribe() -> None:
        await asyncio.sleep(0.05)
        broker.close()

    closer = asyncio.create_task(close_after_subscribe())
    try:
        async with client.stream(
            "GET",
            f"/api/conversations/{conv_id}/stream",
            params={"run_id": run_id, "last_event_id": f"{run_id}-2"},
        ) as resp:
            assert resp.status_code == 200
            body = await resp.aread()
    finally:
        await closer

    events = _parse_sse_events(body.decode())
    deltas = [
        json.loads(e["data"])["delta"]
        for e in events
        if e.get("event") == "content_delta"
    ]
    assert deltas == ["b"], "should only emit events after last_event_id"


# ---------------------------------------------------------------------------
# Scenario B — replay only (broker miss + DB completed row)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_replay_completed_row(client: AsyncClient) -> None:
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start",
         "data": {"id": run_id, "role": "assistant"}},
        {"id": f"{run_id}-2", "event": "content_delta",
         "data": {"delta": "hello"}},
        {"id": f"{run_id}-3", "event": "message_end",
         "data": {"content": "hello", "usage": {}}},
    ]

    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=events_payload,
                last_event_id=f"{run_id}-3",
                status="completed",
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["x-resume-mode"] == "replay"
        assert resp.headers["x-run-id"] == run_id
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    assert [e.get("event") for e in events] == [
        "message_start", "content_delta", "message_end"
    ]
    assert all(e.get("event") != "stale" for e in events)


@pytest.mark.asyncio
async def test_resume_replay_after_id_slices_correctly(
    client: AsyncClient,
) -> None:
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
        {"id": f"{run_id}-2", "event": "content_delta", "data": {"delta": "a"}},
        {"id": f"{run_id}-3", "event": "content_delta", "data": {"delta": "b"}},
        {"id": f"{run_id}-4", "event": "message_end",
         "data": {"content": "ab", "usage": {}}},
    ]
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=events_payload,
                last_event_id=f"{run_id}-4",
                status="completed",
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id, "last_event_id": f"{run_id}-2"},
    ) as resp:
        assert resp.status_code == 200
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    deltas = [
        json.loads(e["data"]).get("delta")
        for e in events
        if e.get("event") == "content_delta"
    ]
    assert deltas == ["b"]


@pytest.mark.asyncio
async def test_resume_replay_uses_last_event_id_header_fallback(
    client: AsyncClient,
) -> None:
    """Query 가 비면 ``Last-Event-ID`` 헤더로 폴백."""
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
        {"id": f"{run_id}-2", "event": "content_delta", "data": {"delta": "a"}},
        {"id": f"{run_id}-3", "event": "content_delta", "data": {"delta": "b"}},
    ]
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=events_payload,
                last_event_id=f"{run_id}-3",
                status="completed",
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
        headers={"Last-Event-ID": f"{run_id}-2"},
    ) as resp:
        assert resp.status_code == 200
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    deltas = [
        json.loads(e["data"]).get("delta")
        for e in events
        if e.get("event") == "content_delta"
    ]
    assert deltas == ["b"]


# ---------------------------------------------------------------------------
# Scenario C — stale streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_stale_streaming_emits_marker(
    client: AsyncClient,
) -> None:
    """broker miss + DB status='streaming' → events 후 ``event: stale`` 발행."""
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
        {"id": f"{run_id}-2", "event": "content_delta", "data": {"delta": "hi"}},
    ]
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=events_payload,
                last_event_id=f"{run_id}-2",
                status="streaming",
            )
        )
        await db.commit()

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["x-resume-mode"] == "replay"
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    assert events[-1]["event"] == "stale"
    stale_data = json.loads(events[-1]["data"])
    assert stale_data["reason"] == "broker_lost"
    assert stale_data["last_event_id"] == f"{run_id}-2"


# ---------------------------------------------------------------------------
# Scenario D — HiTL interrupt pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_interrupt_pending_returns_409(
    client: AsyncClient,
) -> None:
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
        {"id": f"{run_id}-2", "event": "interrupt",
         "data": {"interrupt_id": "abc"}},
    ]
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=events_payload,
                last_event_id=f"{run_id}-2",
                status="streaming",
            )
        )
        await db.commit()

    resp = await client.get(
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RESUME_INTERRUPT_PENDING"


# ---------------------------------------------------------------------------
# Error gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_missing_run_id_returns_404(client: AsyncClient) -> None:
    conv_id = await _seed_conv()
    resp = await client.get(
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_run_id_belongs_to_other_conversation_returns_403(
    client: AsyncClient,
) -> None:
    """A row whose ``conversation_id`` differs from the URL → 403."""
    conv_a = await _seed_conv()
    conv_b = await _seed_conv()
    run_id = str(uuid.uuid4())
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_a,
                assistant_msg_id=run_id,
                events=[{"id": f"{run_id}-1", "event": "message_start",
                          "data": {"id": run_id}}],
                last_event_id=f"{run_id}-1",
                status="completed",
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()

    resp = await client.get(
        f"/api/conversations/{conv_b}/stream",
        params={"run_id": run_id},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "RESUME_FORBIDDEN"


@pytest.mark.asyncio
async def test_resume_unknown_conversation_returns_404(
    client: AsyncClient,
) -> None:
    resp = await client.get(
        f"/api/conversations/{uuid.uuid4()}/stream",
        params={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
