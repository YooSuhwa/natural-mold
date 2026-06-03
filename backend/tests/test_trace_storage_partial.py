"""Tests for W3-out M2 partial flush — ``trace_storage.append_events`` /
``finalize_turn`` lifecycle.

CHECKPOINT.md M2 done-when 일부:
- append_events: insert / merge / dedup-by-id
- finalize_turn: status 갱신, completed_at, linked_message_ids 부착
- record_turn 호환 유지 (test_trace_storage.py가 별도로 검증)
"""

from __future__ import annotations

import uuid

import pytest

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services import trace_storage
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation() -> uuid.UUID:
    async with TestSession() as db:
        existing_user = await db.get(User, TEST_USER_ID)
        if existing_user is None:
            db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=TEST_USER_ID,
            name="Partial Trace Tester",
            description=None,
            system_prompt="...",
            model_id=model.id,
            status="active",
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="t1")
        db.add(conv)
        await db.commit()
        return conv.id


def _chunk(msg_id: str, start_seq: int, count: int) -> list[dict]:
    """Build a list of content_delta-like events with sequential ids."""
    return [
        {
            "id": f"{msg_id}-{start_seq + i}",
            "event": "content_delta",
            "data": {"delta": f"chunk-{start_seq + i}"},
        }
        for i in range(count)
    ]


# --------------------------------------------------------------------------
# append_events — insert + merge
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_events_creates_new_row() -> None:
    conv_id = await _seed_conversation()
    msg_id = "msg-partial-1"
    chunk = _chunk(msg_id, 1, 3)

    async with TestSession() as db:
        record = await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=chunk,
        )
        await db.commit()
        assert record is not None
        assert record.assistant_msg_id == msg_id
        assert record.status == "streaming"
        assert record.last_event_id == f"{msg_id}-3"
        assert record.completed_at is None  # finalize_turn이 아직 안 불림
        assert len(await trace_storage.load_events(db, record)) == 3


@pytest.mark.asyncio
async def test_append_events_merges_into_existing() -> None:
    conv_id = await _seed_conversation()
    msg_id = "msg-partial-2"

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 3),
        )
        await db.commit()

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 4, 3),
        )
        await db.commit()

    async with TestSession() as db:
        fetched = await trace_storage.get_trace_by_msg_id(db, msg_id)
        assert fetched is not None
        ids = [e["id"] for e in fetched.events]
        assert ids == [f"{msg_id}-{i}" for i in range(1, 7)]
        assert fetched.last_event_id == f"{msg_id}-6"
        assert fetched.status == "streaming"


@pytest.mark.asyncio
async def test_append_events_stores_payload_in_append_only_chunks() -> None:
    from sqlalchemy import select

    from app.models.message_event import MessageEvent, MessageEventChunk

    conv_id = await _seed_conversation()
    msg_id = "msg-partial-chunks"

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 2),
        )
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 3, 2),
        )
        await db.commit()

    async with TestSession() as db:
        row = (
            await db.execute(
                select(MessageEvent).where(MessageEvent.assistant_msg_id == msg_id)
            )
        ).scalar_one()
        chunks = (
            await db.execute(
                select(MessageEventChunk)
                .where(MessageEventChunk.message_event_id == row.id)
                .order_by(MessageEventChunk.seq_start)
            )
        ).scalars().all()

        assert row.events == []
        assert [chunk.seq_start for chunk in chunks] == [1, 3]
        assert [chunk.seq_end for chunk in chunks] == [2, 4]
        assert [event["id"] for event in await trace_storage.load_events(db, row)] == [
            f"{msg_id}-1",
            f"{msg_id}-2",
            f"{msg_id}-3",
            f"{msg_id}-4",
        ]


@pytest.mark.asyncio
async def test_append_events_dedups_by_id() -> None:
    """같은 chunk를 두 번 append해도 events에 중복이 생기지 않는다."""
    conv_id = await _seed_conversation()
    msg_id = "msg-partial-3"
    chunk = _chunk(msg_id, 1, 3)

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=chunk,
        )
        await db.commit()

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=chunk,  # same chunk again
        )
        await db.commit()

    async with TestSession() as db:
        fetched = await trace_storage.get_trace_by_msg_id(db, msg_id)
        assert fetched is not None
        ids = [e["id"] for e in fetched.events]
        assert ids == [f"{msg_id}-1", f"{msg_id}-2", f"{msg_id}-3"]


@pytest.mark.asyncio
async def test_append_events_partial_overlap_keeps_only_new() -> None:
    """Boundary 중복 — chunk가 기존 마지막 event를 포함하는 경우."""
    conv_id = await _seed_conversation()
    msg_id = "msg-partial-overlap"

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 3),
        )
        await db.commit()

    async with TestSession() as db:
        # ids 3,4,5 — 3은 기존, 4/5만 새로 추가되어야 함.
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 3, 3),
        )
        await db.commit()

    async with TestSession() as db:
        fetched = await trace_storage.get_trace_by_msg_id(db, msg_id)
        assert fetched is not None
        ids = [e["id"] for e in fetched.events]
        assert ids == [f"{msg_id}-{i}" for i in range(1, 6)]


@pytest.mark.asyncio
async def test_append_events_empty_chunk_is_noop() -> None:
    conv_id = await _seed_conversation()
    async with TestSession() as db:
        result = await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id="never-created",
            events_chunk=[],
        )
        await db.commit()
        assert result is None


# --------------------------------------------------------------------------
# finalize_turn
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_turn_marks_completed() -> None:
    conv_id = await _seed_conversation()
    msg_id = "msg-final-1"

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 2),
        )
        await db.commit()

    async with TestSession() as db:
        record = await trace_storage.finalize_turn(
            db,
            assistant_msg_id=msg_id,
            status="completed",
        )
        await db.commit()
        assert record is not None
        assert record.status == "completed"
        assert record.completed_at is not None


@pytest.mark.asyncio
async def test_finalize_turn_marks_failed() -> None:
    conv_id = await _seed_conversation()
    msg_id = "msg-final-fail"

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 1),
        )
        await db.commit()

    async with TestSession() as db:
        record = await trace_storage.finalize_turn(
            db,
            assistant_msg_id=msg_id,
            status="failed",
        )
        await db.commit()
        assert record is not None
        assert record.status == "failed"
        assert record.completed_at is not None


@pytest.mark.asyncio
async def test_finalize_turn_attaches_linked_message_ids() -> None:
    from app.agent_runtime.message_utils import parse_msg_id

    conv_id = await _seed_conversation()
    msg_id = "msg-final-linked"
    raw_ids = ["raw-x", "raw-y"]
    expected = [str(parse_msg_id(r, conv_id, idx)) for idx, r in enumerate(raw_ids)]

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 1),
        )
        await db.commit()

    async with TestSession() as db:
        record = await trace_storage.finalize_turn(
            db,
            assistant_msg_id=msg_id,
            status="completed",
            raw_msg_ids=raw_ids,
        )
        await db.commit()
        assert record is not None
        assert record.linked_message_ids == expected


@pytest.mark.asyncio
async def test_finalize_turn_returns_none_when_row_missing() -> None:
    """append_events가 한 번도 안 불린 경우 (events 0건). caller가 fallback 결정."""
    async with TestSession() as db:
        result = await trace_storage.finalize_turn(
            db,
            assistant_msg_id="never-existed",
            status="completed",
        )
        await db.commit()
        assert result is None


# --------------------------------------------------------------------------
# record_turn backward-compat — 새로 도입한 status 컬럼 기본값 확인.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_turn_sets_status_completed() -> None:
    """기존 ``record_turn`` 경로는 status='completed' 로 기록되어야 한다."""
    conv_id = await _seed_conversation()
    events = [
        {"id": "msg-rec-1", "event": "message_start", "data": {"id": "msg-rec"}},
        {"id": "msg-rec-2", "event": "message_end", "data": {"usage": {}, "content": ""}},
    ]

    async with TestSession() as db:
        record = await trace_storage.record_turn(
            db, conversation_id=conv_id, events=events
        )
        await db.commit()
        assert record is not None
        assert record.status == "completed"
        assert record.completed_at is not None
        # m34 — updated_at 컬럼이 server_default(now)로 채워져야 함.
        assert record.updated_at is not None


@pytest.mark.asyncio
async def test_record_turn_can_set_status_failed() -> None:
    """Fallback trace persistence must preserve a failed stream status."""
    conv_id = await _seed_conversation()
    events = [
        {"id": "msg-rec-fail-1", "event": "message_start", "data": {"id": "msg-rec-fail"}},
        {"id": "msg-rec-fail-2", "event": "error", "data": {"message": "boom"}},
        {
            "id": "msg-rec-fail-3",
            "event": "message_end",
            "data": {"usage": {}, "content": "", "status": "failed"},
        },
    ]

    async with TestSession() as db:
        record = await trace_storage.record_turn(
            db,
            conversation_id=conv_id,
            events=events,
            status="failed",
        )
        await db.commit()
        assert record is not None
        assert record.status == "failed"
        assert record.completed_at is not None


@pytest.mark.asyncio
async def test_append_events_sets_updated_at_on_insert_and_update() -> None:
    """append_events insert + 후속 update 모두 updated_at 이 채워진다.

    SQLite 는 server_default=CURRENT_TIMESTAMP 가 second-precision이라 두 번
    호출 사이의 차이가 작을 수 있다. 정확한 monotonic 비교 대신 not-None +
    second comparison 으로 회귀 신호를 잡는다.
    """
    conv_id = await _seed_conversation()
    msg_id = "msg-touch"

    async with TestSession() as db:
        record = await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 1, 1),
        )
        await db.commit()
        assert record is not None
        assert record.updated_at is not None

    async with TestSession() as db:
        record = await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=msg_id,
            events_chunk=_chunk(msg_id, 2, 1),
        )
        await db.commit()
        assert record is not None
        assert record.updated_at is not None
        assert len(await trace_storage.load_events(db, record)) == 2
