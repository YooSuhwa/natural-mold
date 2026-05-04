"""W3-out M3 — GET /api/conversations/{id}/stream resume endpoint.

4 분기 검증 (plan 파일 ``M3 — GET resume endpoint``):
- 시나리오 A: live attach (broker hit) → ``X-Resume-Mode: live`` 로 broker
  buffer + 후속 publish 가 stream 으로 전달
- 시나리오 B: replay only (broker miss + DB row completed) → ``X-Resume-Mode:
  replay`` 로 events 슬라이스 만 emit
- 시나리오 C: stale streaming (broker miss + DB row status='streaming') →
  events emit 후 ``event: stale`` 마커 발행
- 시나리오 D: HiTL interrupt pending → ``409 RESUME_INTERRUPT_PENDING``

가드 분기는 모두 ``404 RESUME_NOT_FOUND`` 단일 응답으로 통일 (rules/security.md
— enumeration oracle 방지). 분기 구분은 서버 로그로만:
- conv 없음
- ownership 실패 (다른 user 의 agent)
- DB row 없음
- broker live 인데 conv_id 불일치 (cross-tenant)
- DB row 가 다른 conversation 소속

``Last-Event-ID`` 헤더는 query 가 비면 fallback.
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
async def test_resume_replay_header_is_case_insensitive(
    client: AsyncClient,
) -> None:
    """일부 reverse proxy 가 헤더를 lowercase 로 전달 — alias 매칭 회귀."""
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
        headers={"last-event-id": f"{run_id}-2"},  # lowercase
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
async def test_resume_replay_skips_corrupt_event_without_name(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M-2: ``event`` 필드가 비어있는 corrupt row 항목은 silent emit 금지."""
    import logging

    caplog.set_level(logging.WARNING, logger="app.routers.conversations")
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
        # corrupt row — event 필드 누락
        {"id": f"{run_id}-2", "data": {"delta": "??"}},
        {"id": f"{run_id}-3", "event": "content_delta", "data": {"delta": "ok"}},
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
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    # corrupt evt 는 emit 안 됨 — message_start + content_delta(ok) 만.
    assert [e.get("event") for e in events] == ["message_start", "content_delta"]
    assert any(
        "stream_resume skip corrupt evt" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_resume_stale_payload_falls_back_when_last_event_id_null(
    client: AsyncClient,
) -> None:
    """M-3: row.last_event_id 가 None 이면 events 마지막 id 로 fallback.

    둘 다 없으면 ``reason='broker_lost_no_id'`` 로 분기해 client NPE 회피.
    """
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    events_payload = [
        {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
    ]
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=events_payload,
                last_event_id=None,  # corrupt row — last_event_id 안 채워짐
                status="streaming",
            )
        )
        await db.commit()

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    ) as resp:
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    stale = next(e for e in events if e.get("event") == "stale")
    stale_data = json.loads(stale["data"])
    # events 마지막 id 로 fallback 성공.
    assert stale_data["last_event_id"] == f"{run_id}-1"
    assert stale_data["reason"] == "broker_lost"


@pytest.mark.asyncio
async def test_resume_stale_payload_no_id_when_events_empty_after_slice(
    client: AsyncClient,
) -> None:
    """events 가 빈 채로 stale → ``broker_lost_no_id`` reason."""
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=conv_id,
                assistant_msg_id=run_id,
                events=[],  # 빈 events
                last_event_id=None,
                status="streaming",
            )
        )
        await db.commit()

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    ) as resp:
        body = await resp.aread()

    events = _parse_sse_events(body.decode())
    stale = next(e for e in events if e.get("event") == "stale")
    stale_data = json.loads(stale["data"])
    assert stale_data["last_event_id"] is None
    assert stale_data["reason"] == "broker_lost_no_id"


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
# Error gates — 모두 단일 RESUME_NOT_FOUND (enumeration oracle 방지)
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
async def test_resume_db_row_belongs_to_other_conversation_returns_404(
    client: AsyncClient,
) -> None:
    """DB row 의 ``conversation_id`` 가 URL 과 다르면 404 (oracle 방지)."""
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
    # 응답은 row 미존재 케이스와 외부적으로 동일 (RESUME_NOT_FOUND).
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_live_broker_belongs_to_other_conversation_returns_404(
    client: AsyncClient,
) -> None:
    """broker live 인데 broker.conversation_id 가 URL 과 다르면 404."""
    conv_a = await _seed_conv()
    conv_b = await _seed_conv()
    run_id = str(uuid.uuid4())
    # conv_a 에 live broker 등록.
    event_broker.registry.get_or_create(
        run_id, conversation_id=str(conv_a)
    )
    # conv_b URL 로 GET → broker live 분기에서 conv_id mismatch.
    resp = await client.get(
        f"/api/conversations/{conv_b}/stream",
        params={"run_id": run_id},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_live_broker_with_none_conversation_id_returns_404(
    client: AsyncClient,
) -> None:
    """broker.conversation_id 가 None 이면 fail-closed → 404."""
    conv_id = await _seed_conv()
    run_id = str(uuid.uuid4())
    # 일부러 conv_id 생략한 broker — 비정상 등록 path 시뮬레이션.
    event_broker.registry.get_or_create(run_id, conversation_id=None)

    resp = await client.get(
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_unknown_conversation_returns_404(
    client: AsyncClient,
) -> None:
    """Unknown conv_id → 404 RESUME_NOT_FOUND (역시 단일 응답)."""
    resp = await client.get(
        f"/api/conversations/{uuid.uuid4()}/stream",
        params={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_logs_reject_reason(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """가드 분기는 외부 응답 통일하되 서버 로그로 reason 구분 가능해야 함."""
    import logging

    caplog.set_level(logging.INFO, logger="app.routers.conversations")
    conv_id = await _seed_conv()
    resp = await client.get(
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    # reason=row_missing 분기 로그가 남아야 함 (conv 존재 + DB row 없음).
    assert any(
        "stream_resume reject" in r.message and "reason=row_missing" in r.message
        for r in caplog.records
    )


# N-3 (client disconnect → listener cleanup), 갭 (multi-listener fan-out /
# ring buffer overflow + after_id evict) 는 모두 unit-level 의존이라
# tests/agent_runtime/test_event_broker.py 에서 broker AsyncGenerator 를
# 직접 검증한다 (httpx ASGITransport 의 disconnect 타이밍은 결정적이지 않아
# router 통합 테스트로 잡으면 hang 위험). multi-listener fan-out 과 ring
# overflow 후 subscribe 는 이미 기존 broker unit test 가 보장:
# - test_multiple_listeners_broadcast
# - test_ring_buffer_drops_oldest
# - test_subscribe_after_ring_eviction_returns_buffer_only
