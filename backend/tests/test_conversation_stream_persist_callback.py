"""BE-P5(d) — ``build_persist_callback`` 의 run-scoped seen_event_ids 캐시 계약.

partial flush 마다 누적 chunk 의 event id 전체를 재 SELECT 하던 O(T²/64)
재로드를 제거하면서 지켜야 하는 불변식:

- 정상 경로: 첫 flush 에서 1회 시드 후 증분 유지 (이후 DB 재로드 0회)
- 중복 chunk 재전송: 캐시 기준으로 dedup (유실도 중복도 없음)
- 실패 경로: 캐시 리셋 → 재시도 chunk 는 DB 재로드 경로로 dedup
  (캐시가 DB 를 앞서면 재시도 이벤트가 조용히 유실되는 방향이 위험)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services import trace_storage
from app.services.conversation_stream_service import build_persist_callback
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
            name="Persist Callback Tester",
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


def _chunk(run_id: str, start_seq: int, count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{run_id}-{start_seq + i}",
            "event": "content_delta",
            "data": {"delta": f"chunk-{start_seq + i}"},
        }
        for i in range(count)
    ]


async def _stored_ids(run_id: str) -> list[str]:
    async with TestSession() as db:
        record = await trace_storage.get_trace_by_msg_id(db, run_id)
        assert record is not None
        return [event["id"] for event in record.events]


@pytest.mark.asyncio
async def test_persist_callback_seeds_once_then_dedups_without_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_id = await _seed_conversation()
    run_id = "run-cb-cache"
    callback = build_persist_callback(conv_id, run_id)

    seed_calls = 0
    real_seed = trace_storage.load_persisted_event_ids

    async def counting_seed(*args: Any, **kwargs: Any) -> set[str]:
        nonlocal seed_calls
        seed_calls += 1
        return await real_seed(*args, **kwargs)

    monkeypatch.setattr(trace_storage, "load_persisted_event_ids", counting_seed)

    async def _explode(*_args: object, **_kwargs: object) -> set[str]:
        raise AssertionError("캐시 경로에서 누적 chunk DB 재로드가 발생하면 안 된다")

    monkeypatch.setattr(trace_storage, "_load_existing_event_ids", _explode)

    await callback(_chunk(run_id, 1, 3))
    # boundary 중복(id 3) + 신규(4-5) 재전송 — 캐시만으로 dedup 되어야 한다.
    await callback(_chunk(run_id, 3, 3))

    assert seed_calls == 1
    assert await _stored_ids(run_id) == [f"{run_id}-{i}" for i in range(1, 6)]


@pytest.mark.asyncio
async def test_persist_callback_failure_resets_cache_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_id = await _seed_conversation()
    run_id = "run-cb-retry"
    callback = build_persist_callback(conv_id, run_id)

    seed_calls = 0
    real_seed = trace_storage.load_persisted_event_ids

    async def counting_seed(*args: Any, **kwargs: Any) -> set[str]:
        nonlocal seed_calls
        seed_calls += 1
        return await real_seed(*args, **kwargs)

    monkeypatch.setattr(trace_storage, "load_persisted_event_ids", counting_seed)

    await callback(_chunk(run_id, 1, 3))
    assert seed_calls == 1

    real_append = trace_storage.append_events
    fail_once = {"armed": True}

    async def flaky_append(*args: Any, **kwargs: Any) -> Any:
        if fail_once["armed"]:
            fail_once["armed"] = False
            raise RuntimeError("transient DB failure")
        return await real_append(*args, **kwargs)

    monkeypatch.setattr(trace_storage, "append_events", flaky_append)

    with pytest.raises(RuntimeError, match="transient DB failure"):
        await callback(_chunk(run_id, 4, 2))

    # 스트리밍 쪽 재시도 의미론 — 실패 chunk 가 buffer 앞에 복원되어 다음
    # flush 에 신규 이벤트와 함께 재전송된다.
    await callback([*_chunk(run_id, 4, 2), *_chunk(run_id, 6, 1)])

    # 실패는 캐시를 무효화해 재시도가 DB 재시드 경로를 타야 한다 — 캐시가
    # DB 를 앞서 있으면(실패 chunk 를 이미 본 것으로 오인) 재시도 이벤트가
    # dedup 으로 조용히 유실된다.
    assert seed_calls == 2
    assert await _stored_ids(run_id) == [f"{run_id}-{i}" for i in range(1, 7)]
