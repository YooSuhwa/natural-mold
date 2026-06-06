"""Tests for W5 TraceStorage — message_events service + GET /traces."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services import trace_storage
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation(*, owner_id: uuid.UUID = TEST_USER_ID) -> uuid.UUID:
    """Insert minimal User + Model + Agent + Conversation, return conversation_id."""
    async with TestSession() as db:
        existing_user = await db.get(User, owner_id)
        if existing_user is None:
            db.add(User(id=owner_id, email=f"{owner_id}@test.com", name="Test"))
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=owner_id,
            name="Trace Tester",
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


def _events_for_msg(msg_id: str, *, count: int = 3) -> list[dict]:
    """Build a representative event sequence: start + N delta + end."""
    events: list[dict] = [
        {"id": f"{msg_id}-1", "event": "message_start", "data": {"id": msg_id}},
    ]
    for i in range(count):
        events.append(
            {
                "id": f"{msg_id}-{i + 2}",
                "event": "content_delta",
                "data": {"delta": f"chunk-{i}"},
            }
        )
    events.append(
        {
            "id": f"{msg_id}-{count + 2}",
            "event": "message_end",
            "data": {"usage": {}, "content": "".join(f"chunk-{i}" for i in range(count))},
        }
    )
    return events


@pytest.mark.asyncio
async def test_record_turn_round_trip() -> None:
    conv_id = await _seed_conversation()
    msg_id = "msg-1"
    events = _events_for_msg(msg_id, count=2)

    async with TestSession() as db:
        record = await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        await db.commit()
        assert record is not None
        assert record.assistant_msg_id == msg_id
        assert record.last_event_id == f"{msg_id}-4"  # start + 2 deltas + end
        assert record.completed_at is not None

    async with TestSession() as db:
        fetched = await trace_storage.get_trace_by_msg_id(db, msg_id)
        assert fetched is not None
        assert len(fetched.events) == 4
        assert fetched.events[0]["event"] == "message_start"
        assert fetched.events[-1]["event"] == "message_end"


@pytest.mark.asyncio
async def test_record_turn_empty_events_is_noop() -> None:
    conv_id = await _seed_conversation()
    async with TestSession() as db:
        result = await trace_storage.record_turn(db, conversation_id=conv_id, events=[])
        await db.commit()
        assert result is None

    async with TestSession() as db:
        all_traces = await trace_storage.get_traces_for_conversation(db, conv_id)
        assert all_traces == []


@pytest.mark.asyncio
async def test_record_turn_without_message_start_falls_back_to_uuid() -> None:
    """비정상 스트림 — message_start 없이 error만 emit된 케이스."""
    conv_id = await _seed_conversation()
    events = [
        {"id": "x-1", "event": "error", "data": {"message": "graph crashed"}},
    ]
    async with TestSession() as db:
        record = await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        await db.commit()
        assert record is not None
        # Fallback uuid는 RFC4122 v4 형식
        uuid.UUID(record.assistant_msg_id)
        assert record.last_event_id == "x-1"


@pytest.mark.asyncio
async def test_get_traces_for_conversation_orders_by_created_at() -> None:
    """여러 turn은 created_at 기준 오름차순으로 반환."""
    conv_id = await _seed_conversation()

    async with TestSession() as db:
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("msg-A")
        )
        await db.commit()
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("msg-B")
        )
        await db.commit()
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("msg-C")
        )
        await db.commit()

    async with TestSession() as db:
        traces = await trace_storage.get_traces_for_conversation(db, conv_id)
        assert [t.assistant_msg_id for t in traces] == ["msg-A", "msg-B", "msg-C"]


@pytest.mark.asyncio
async def test_record_turn_unique_assistant_msg_id() -> None:
    """같은 assistant_msg_id로 2번 record하면 unique 제약으로 실패."""
    conv_id = await _seed_conversation()
    events = _events_for_msg("dup-1")

    async with TestSession() as db:
        await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        await db.commit()

    from sqlalchemy.exc import IntegrityError

    async with TestSession() as db:
        await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_get_traces_endpoint_returns_persisted_turns(client: AsyncClient) -> None:
    """GET /api/conversations/{id}/traces — service 레이어로 시드 후 조회."""
    conv_id = await _seed_conversation()
    async with TestSession() as db:
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("msg-1")
        )
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("msg-2", count=1)
        )
        await db.commit()

    response = await client.get(f"/api/conversations/{conv_id}/traces")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["assistant_msg_id"] == "msg-1"
    assert body[1]["assistant_msg_id"] == "msg-2"
    # event shape
    assert body[0]["events"][0]["event"] == "message_start"
    assert body[0]["last_event_id"].startswith("msg-1-")


@pytest.mark.asyncio
async def test_get_traces_endpoint_requires_auth(raw_client: AsyncClient) -> None:
    conv_id = await _seed_conversation()
    async with TestSession() as db:
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("private-msg")
        )
        await db.commit()

    response = await raw_client.get(f"/api/conversations/{conv_id}/traces")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_traces_endpoint_hides_other_users_conversation(
    client: AsyncClient,
) -> None:
    conv_id = await _seed_conversation(owner_id=uuid.uuid4())
    async with TestSession() as db:
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("foreign-msg")
        )
        await db.commit()

    response = await client.get(f"/api/conversations/{conv_id}/traces")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_traces_endpoint_404_for_unknown_conversation(
    client: AsyncClient,
) -> None:
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/conversations/{fake_id}/traces")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_traces_endpoint_empty_when_no_turns_recorded(
    client: AsyncClient,
) -> None:
    """대화는 있지만 trace 0건이면 빈 배열 (200)."""
    conv_id = await _seed_conversation()
    response = await client.get(f"/api/conversations/{conv_id}/traces")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_record_turn_persists_linked_message_ids() -> None:
    """W6 정확도 — raw_msg_ids → parsed UUID로 linked_message_ids에 저장."""
    from app.agent_runtime.message_utils import parse_msg_id

    conv_id = await _seed_conversation()
    raw_ids = ["run-abc-1", "run-abc-2"]
    expected_uuids = [str(parse_msg_id(raw, conv_id, idx)) for idx, raw in enumerate(raw_ids)]

    async with TestSession() as db:
        record = await trace_storage.record_turn(
            db,
            conversation_id=conv_id,
            events=_events_for_msg("turn-with-linked"),
            raw_msg_ids=raw_ids,
        )
        await db.commit()
        assert record is not None
        assert record.linked_message_ids == expected_uuids


@pytest.mark.asyncio
async def test_finalize_turn_persists_external_trace_correlation() -> None:
    conv_id = await _seed_conversation()
    run_id = "run-langfuse"

    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=run_id,
            events_chunk=_events_for_msg(run_id, count=1),
        )
        await db.commit()

    async with TestSession() as db:
        record = await trace_storage.finalize_turn(
            db,
            assistant_msg_id=run_id,
            status="completed",
            conversation_id=conv_id,
            external_trace_provider="langfuse",
            external_trace_id="lf-trace-123",
            external_trace_url="https://langfuse.local/project/moldy/traces/lf-trace-123",
        )
        await db.commit()

        assert record is not None
        assert record.external_trace_provider == "langfuse"
        assert record.external_trace_id == "lf-trace-123"
        assert record.external_trace_url is not None
        assert record.external_trace_url.endswith("/lf-trace-123")


@pytest.mark.asyncio
async def test_record_turn_persists_external_trace_correlation() -> None:
    conv_id = await _seed_conversation()

    async with TestSession() as db:
        record = await trace_storage.record_turn(
            db,
            conversation_id=conv_id,
            events=_events_for_msg("one-shot-langfuse", count=1),
            external_trace_provider="langfuse",
            external_trace_id="lf-trace-one-shot",
            external_trace_url="https://langfuse.local/project/moldy/traces/lf-trace-one-shot",
        )
        await db.commit()

        assert record is not None
        assert record.external_trace_provider == "langfuse"
        assert record.external_trace_id == "lf-trace-one-shot"
        assert record.external_trace_url is not None
        assert record.external_trace_url.endswith("/lf-trace-one-shot")


@pytest.mark.asyncio
async def test_record_turn_linked_ids_default_none() -> None:
    """raw_msg_ids 미전달 시 linked_message_ids는 None (m32 호환 폴백)."""
    conv_id = await _seed_conversation()
    async with TestSession() as db:
        record = await trace_storage.record_turn(
            db,
            conversation_id=conv_id,
            events=_events_for_msg("no-linked"),
        )
        await db.commit()
        assert record is not None
        assert record.linked_message_ids is None


@pytest.mark.asyncio
async def test_record_turn_appends_to_session_without_committing() -> None:
    """record_turn 자체는 commit 안 함 — caller가 commit 책임."""
    conv_id = await _seed_conversation()
    events = _events_for_msg("uncommitted")

    db: AsyncSession
    async with TestSession() as db:
        record = await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        assert record is not None
        # commit 없이 세션 종료 — 데이터 영속 안 됨
        await db.rollback()

    async with TestSession() as db:
        rows = await trace_storage.get_traces_for_conversation(db, conv_id)
        assert rows == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_message_event_cascade_delete_with_conversation() -> None:
    """conversation 삭제 시 message_events도 함께 삭제 (FK CASCADE).

    Postgres 한정 — aiosqlite는 기본적으로 ``PRAGMA foreign_keys=OFF``라서
    ``ondelete='CASCADE'``가 무시된다. 마이그레이션에 선언된 cascade가 실제로
    동작하는지 검증하려면 라이브 PG가 필요하므로 integration 마커를 단다.
    """
    conv_id = await _seed_conversation()
    async with TestSession() as db:
        await trace_storage.record_turn(
            db, conversation_id=conv_id, events=_events_for_msg("msg-x")
        )
        await db.commit()

    async with TestSession() as db:
        # 직접 SQL로 conversation 삭제
        from sqlalchemy import select

        conv = (
            await db.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one()
        await db.delete(conv)
        await db.commit()

    async with TestSession() as db:
        from sqlalchemy import select

        rows = (await db.execute(select(MessageEvent))).scalars().all()
        assert list(rows) == []
