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

W3-out M6 — POST → GET roundtrip 통합. 라우터-단위 시나리오는 위에서 broker /
DB row 를 합성으로 주입했지만 M6 는 실제 ``POST /messages`` 로 broker 를
등록하고 partial flush 를 실재 DB(in-memory aiosqlite) 에 적재한 뒤 ``GET
/stream`` 으로 이어 받는 cross-handler invariant 를 잡는다 (live attach 도중
abort, broker 강제 evict 후 DB replay, finalize 미실행 시 stale, interrupt 가
DB 에 남은 채로 resume). 핵심은 ``async_session`` 을 TestSession 으로
monkeypatch — partial flush / finalize_turn 이 conftest in-memory DB 와 같은
엔진을 쓰지 않으면 GET 측 ``trace_storage.get_trace_by_msg_id`` 가 빈 결과를
받아 RESUME_NOT_FOUND 로 떨어진다.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.agent_runtime import event_broker
from app.agent_runtime.streaming import format_sse
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


# ---------------------------------------------------------------------------
# W3-out M6 — end-to-end POST → GET resume integration
# ---------------------------------------------------------------------------
#
# 위 시나리오들은 broker / DB row 를 합성으로 주입한 뒤 GET 만 단독 검증한다.
# M6 는 진짜 POST 핸들러를 통과하면서 (a) `_prepare_stream_context` 가 broker
# 를 등록하고 (b) partial flush 가 DB 에 status='streaming' row 를 만들고
# (c) finalize_turn 이 종결 상태로 마감한다는 cross-handler invariant 를 묶어
# 검증한다. 라우터 변경 시 한 곳만 어긋나도 잡힌다.


def _build_events(run_id: str) -> list[dict[str, Any]]:
    """E2E 시나리오 공용 — 4-event happy path."""
    return [
        {"id": f"{run_id}-1", "event": "message_start",
         "data": {"id": run_id, "role": "assistant"}},
        {"id": f"{run_id}-2", "event": "content_delta",
         "data": {"delta": "hi"}},
        {"id": f"{run_id}-3", "event": "content_delta",
         "data": {"delta": " world"}},
        {"id": f"{run_id}-4", "event": "message_end",
         "data": {"content": "hi world", "usage": {}}},
    ]


def _make_executor_simulator(
    events_factory,  # callable: (run_id) -> list[event dict]
    *,
    pause_after: int | None = None,
    pause_event: asyncio.Event | None = None,
    close_broker_at_end: bool = True,
    captured: dict[str, Any] | None = None,
):
    """Build a mock for ``execute_agent_stream`` that simulates the streaming
    layer's dual-write contract (``broker.publish_nowait`` + ``trace_sink``
    append + ``persist_callback`` flush + SSE yield). ``stream_agent_response``
    의 finally 가 broker.close 를 호출하지만, 우리는 실행기 자체를 패치하므로
    그 책임을 시뮬레이터가 흉내낸다.

    pause_after: 처음 N개 emit 후 ``pause_event.wait()`` 로 멈춤. live attach
    시나리오 — POST 가 mid-stream 에 머물러 broker 가 살아있는 동안 GET 이
    들어오는 경합을 결정적으로 재현한다.
    """

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        broker = kwargs["broker"]
        persist_cb = kwargs["persist_callback"]
        trace_sink = kwargs["trace_sink"]
        run_id = kwargs["run_id"]
        if captured is not None:
            captured["run_id"] = run_id
            captured["broker"] = broker

        events = events_factory(run_id)
        try:
            for idx, evt in enumerate(events):
                broker.publish_nowait(evt)
                trace_sink.append(evt)
                # partial flush — async_session 이 TestSession 으로 patch 된
                # 상태에서만 in-memory DB 에 row 가 생긴다.
                await persist_cb([evt])
                yield format_sse(
                    evt["event"], evt["data"], event_id=evt["id"]
                )
                if pause_after is not None and idx + 1 == pause_after:
                    assert pause_event is not None
                    await pause_event.wait()
        finally:
            if close_broker_at_end:
                broker.close()

    return mock_stream


async def _wait_for(
    predicate,
    *,
    timeout: float = 2.0,  # noqa: ASYNC109 — bespoke poll helper, asyncio.timeout cancel 의미와 다름
    interval: float = 0.01,
) -> None:
    """Poll ``predicate`` until truthy or timeout — race-free fixture sync."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("timeout waiting for predicate")


@pytest.fixture
def patch_async_session(monkeypatch: pytest.MonkeyPatch):
    """`_build_persist_callback` / `_finalize_trace` 가 conftest in-memory DB
    와 동일 엔진을 쓰도록 router 모듈 안의 ``async_session`` 을 TestSession 으로
    교체. 미patch 시 partial flush 가 production DB(설정 안 됨)로 향해 silent
    drop → GET resume 가 row 를 못 찾아 RESUME_NOT_FOUND 로 빠진다.
    """
    monkeypatch.setattr(
        "app.routers.conversations.async_session", TestSession
    )


@pytest.mark.asyncio
async def test_e2e_post_inflight_get_attaches_live_and_receives_tail(
    client: AsyncClient,
    patch_async_session: None,
) -> None:
    """A: POST 가 mid-stream 일 때 GET 이 들어오면 broker live 로 attach,
    누락된 prefix 를 buffer 에서 replay 한 뒤 라이브 tail 까지 이어 받는다."""
    conv_id = await _seed_conv()
    captured: dict[str, Any] = {}
    pause = asyncio.Event()
    mock_stream = _make_executor_simulator(
        _build_events,
        pause_after=2,
        pause_event=pause,
        captured=captured,
    )

    async def consume_post() -> None:
        # POST 는 mock 의 pause 가 풀릴 때까지 mid-stream 에 머문다 — pause.set
        # 이전에 abort 하지 않고 끝까지 함께 흘려 보낸다 (둘 다 close 까지 따라감).
        async with client.stream(
            "POST",
            f"/api/conversations/{conv_id}/messages",
            json={"content": "go"},
        ) as resp:
            assert resp.status_code == 200
            captured["post_run_id_header"] = resp.headers["x-run-id"]
            async for _ in resp.aiter_text():
                pass

    with patch(
        "app.routers.conversations.execute_agent_stream", side_effect=mock_stream
    ):
        post_task = asyncio.create_task(consume_post())
        try:
            # mock 이 message_start + content_delta 두 개 를 publish 하고 pause 에
            # 걸렸는지 확인 — 이 시점에 broker buffer 는 2 events, broker live.
            await _wait_for(
                lambda: (
                    "broker" in captured
                    and len(captured["broker"]._buffer) >= 2  # noqa: SLF001
                )
            )
            run_id = captured["run_id"]
            broker_live = captured["broker"]
            assert not broker_live.is_closed

            async def consume_get() -> bytes:
                async with client.stream(
                    "GET",
                    f"/api/conversations/{conv_id}/stream",
                    params={"run_id": run_id},
                ) as resp:
                    assert resp.status_code == 200
                    assert resp.headers["x-resume-mode"] == "live"
                    assert resp.headers["x-run-id"] == run_id
                    return await resp.aread()

            get_task = asyncio.create_task(consume_get())
            # GET listener 가 broker.subscribe 에 등록될 때까지 짧게 양보 —
            # pause.set 직후 mock 이 곧장 late events 를 publish 해도 listener
            # queue 에 fan-out 되어야 한다.
            #
            # Race-free invariant: ``EventBroker.subscribe`` 는 첫 ``__anext__``
            # 에서 ``listeners.add(queue)`` → ``buffer snapshot`` → buffer
            # 슬라이스 yield 순서를 await 없이 (단일 sync 청크) 처리한다. 외부
            # observer 가 ``len(_listeners) >= 1`` 을 관측한 시점에 listener
            # 등록 + snapshot 둘 다 완료된 상태이며, 이후 ``publish_nowait`` 은
            # 무조건 queue 로 fan-out + ``yielded_ids`` dedup 으로 boundary
            # 중복도 차단된다 (event_broker.py:194-240).
            await _wait_for(
                lambda: len(broker_live._listeners) >= 1  # noqa: SLF001
            )
            pause.set()
            get_body = await get_task
        finally:
            pause.set()
            await asyncio.wait_for(post_task, timeout=2.0)

    sse_events = _parse_sse_events(get_body.decode())
    deltas = [
        json.loads(e["data"]).get("delta")
        for e in sse_events
        if e.get("event") == "content_delta"
    ]
    # buffer replay (hi) + 라이브 tail ( world) — 둘 다 GET 에 도달.
    assert deltas == ["hi", " world"]
    assert sse_events[-1]["event"] == "message_end"
    # POST header 의 run_id 와 mock 이 받은 run_id 가 일치 — `_prepare_stream
    # _context` 가 한 turn 에서 같은 id 로 broker / persist / 헤더를 통일.
    assert captured["post_run_id_header"] == captured["run_id"]


@pytest.mark.asyncio
async def test_e2e_post_completed_then_broker_evicted_get_replays_from_db(
    client: AsyncClient,
    patch_async_session: None,
) -> None:
    """B: POST 가 정상 종료 → ``registry.evict_expired(ttl_seconds=0)`` 으로
    closed broker 강제 회수 → GET 이 들어오면 DB replay 분기로 떨어진다."""
    conv_id = await _seed_conv()
    captured: dict[str, Any] = {}
    mock_stream = _make_executor_simulator(_build_events, captured=captured)

    with patch(
        "app.routers.conversations.execute_agent_stream", side_effect=mock_stream
    ):
        async with client.stream(
            "POST",
            f"/api/conversations/{conv_id}/messages",
            json={"content": "hi"},
        ) as resp:
            assert resp.status_code == 200
            run_id_header = resp.headers["x-run-id"]
            await resp.aread()

    run_id = captured["run_id"]
    assert run_id == run_id_header

    # broker 는 mock finally 에서 close 되어 evict 후보. ttl=0 으로 강제 회수.
    broker = event_broker.registry.get(run_id)
    assert broker is not None and broker.is_closed
    evicted = event_broker.registry.evict_expired(ttl_seconds=0)
    assert evicted >= 1
    assert event_broker.registry.get(run_id) is None

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["x-resume-mode"] == "replay"
        body = await resp.aread()

    sse_events = _parse_sse_events(body.decode())
    assert [e.get("event") for e in sse_events] == [
        "message_start", "content_delta", "content_delta", "message_end"
    ]
    deltas = [
        json.loads(e["data"]).get("delta")
        for e in sse_events
        if e.get("event") == "content_delta"
    ]
    assert deltas == ["hi", " world"]
    assert all(e.get("event") != "stale" for e in sse_events)


@pytest.mark.asyncio
async def test_e2e_post_killed_before_finalize_get_emits_stale(
    client: AsyncClient,
    patch_async_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C: partial flush 로 status='streaming' row 가 적재된 상태에서 backend
    가 finalize 전에 죽은 케이스. ``_finalize_trace`` 를 no-op 으로 패치해 row
    가 'streaming' 상태로 남도록 해 broker miss → DB replay → ``event: stale``
    경로를 검증한다.
    """
    conv_id = await _seed_conv()
    captured: dict[str, Any] = {}

    async def _no_finalize(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "app.routers.conversations._finalize_trace", _no_finalize
    )

    def events_no_end(run_id: str) -> list[dict[str, Any]]:
        # message_end 누락 — 백엔드가 도중에 죽은 것을 시뮬레이션.
        return [
            {"id": f"{run_id}-1", "event": "message_start",
             "data": {"id": run_id, "role": "assistant"}},
            {"id": f"{run_id}-2", "event": "content_delta",
             "data": {"delta": "partial"}},
        ]

    mock_stream = _make_executor_simulator(events_no_end, captured=captured)

    with patch(
        "app.routers.conversations.execute_agent_stream", side_effect=mock_stream
    ):
        async with client.stream(
            "POST",
            f"/api/conversations/{conv_id}/messages",
            json={"content": "go"},
        ) as resp:
            assert resp.status_code == 200
            await resp.aread()

    run_id = captured["run_id"]
    # finalize 가 no-op 이라 row.status 는 partial flush 가 적은 'streaming'
    # 그대로. broker 는 mock finally 에서 close + ttl=0 으로 회수. 추가로
    # ``_clear()`` 까지 호출해 registry dict 자체를 비워 — 실제 SIGKILL 후
    # backend 재기동 시점 (broker dict 빈 상태) 과 동일한 invariant 를 만든다.
    event_broker.registry.evict_expired(ttl_seconds=0)
    event_broker.registry._clear()  # noqa: SLF001 — crash-after-restart 시뮬
    assert event_broker.registry.get(run_id) is None
    async with TestSession() as db:
        from sqlalchemy import select

        record = (
            await db.execute(
                select(MessageEvent).where(
                    MessageEvent.assistant_msg_id == run_id
                )
            )
        ).scalar_one()
        assert record.status == "streaming"

    async with client.stream(
        "GET",
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["x-resume-mode"] == "replay"
        body = await resp.aread()

    sse_events = _parse_sse_events(body.decode())
    assert sse_events[-1]["event"] == "stale"
    stale_payload = json.loads(sse_events[-1]["data"])
    assert stale_payload["reason"] == "broker_lost"
    assert stale_payload["last_event_id"] == f"{run_id}-2"


@pytest.mark.asyncio
async def test_e2e_post_emits_interrupt_then_get_returns_409(
    client: AsyncClient,
    patch_async_session: None,
) -> None:
    """D: POST 가 message_end 없이 ``interrupt`` 만 emit 한 채 종료하면 GET
    resume 은 graph 가 HiTL 응답을 기다리는 신호로 인식해 409 로 차단한다
    (client 는 ``/messages/resume`` 으로 와야 함).
    """
    conv_id = await _seed_conv()
    captured: dict[str, Any] = {}

    def events_with_interrupt(run_id: str) -> list[dict[str, Any]]:
        return [
            {"id": f"{run_id}-1", "event": "message_start",
             "data": {"id": run_id, "role": "assistant"}},
            {"id": f"{run_id}-2", "event": "interrupt",
             "data": {"interrupt_id": "abc", "value": "approve?"}},
        ]

    mock_stream = _make_executor_simulator(
        events_with_interrupt, captured=captured
    )

    with patch(
        "app.routers.conversations.execute_agent_stream", side_effect=mock_stream
    ):
        async with client.stream(
            "POST",
            f"/api/conversations/{conv_id}/messages",
            json={"content": "do thing"},
        ) as resp:
            assert resp.status_code == 200
            await resp.aread()

    run_id = captured["run_id"]
    # broker 는 mock finally 에서 close. ttl=0 으로 회수해 확실히 broker miss
    # 경로로 떨어지게 한다 (broker live 였다면 subscribe 로 가서 409 가 안 남).
    event_broker.registry.evict_expired(ttl_seconds=0)
    assert event_broker.registry.get(run_id) is None

    resp = await client.get(
        f"/api/conversations/{conv_id}/stream",
        params={"run_id": run_id},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RESUME_INTERRUPT_PENDING"
