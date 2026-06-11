from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import (
    conversation_run_service,
    conversation_run_worker,
    conversation_stream_service,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
async def test_publish_error_persists_same_event_ids_as_live_broker() -> None:
    ctx = conversation_stream_service.prepare_stream_context(
        uuid.uuid4(),
        run_id="run-error-id-stability",
    )
    persisted: list[dict[str, Any]] = []

    async def capture(events: list[dict[str, Any]]) -> None:
        persisted.extend(events)

    ctx = ctx._replace(persist_cb=capture)

    await conversation_run_worker._publish_error(ctx, "boom")

    live_events = list(ctx.broker._buffer)
    assert [event["id"] for event in persisted] == [event["id"] for event in live_events]
    assert [event["event"] for event in persisted] == [event["event"] for event in live_events]


@pytest.mark.asyncio
async def test_cancel_before_running_finalizes_run_as_canceled() -> None:
    """cancel 이 워커 기동 전(queued)에 커밋되면 failed 가 아니라 canceled 로 종료."""
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conv.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="cancel before start",
        )
        # cancel API 가 워커보다 먼저 queued -> canceling 을 커밋한 상황 재현.
        await conversation_run_service.request_cancel_run(db, run)
        await db.commit()
        run_id = run.id

    executor_called = False

    async def never_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        nonlocal executor_called
        executor_called = True
        if False:  # pragma: no cover - async generator 형태 유지
            yield ""

    user = CurrentUser(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        is_super_user=True,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="cancel-race-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(Any, object()),
        user=user,
        input_payload={"content": "x"},
        moldy_source="chat",
        executor_fn=never_stream,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    assert executor_called is False
    async with TestSession() as db:
        refreshed = await db.get(ConversationRun, run_id)
        assert refreshed is not None
        assert refreshed.status == "canceled"
        assert refreshed.error_code is None
        assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_worker_skips_finalization_when_run_already_terminal() -> None:
    """sweep 등이 먼저 terminal 로 보낸 run 은 워커가 재finalize 하지 않는다."""
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conv.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="already stale",
        )
        await conversation_run_service.transition_run(
            db,
            run,
            "stale",
            error_code="stale_heartbeat",
            error_message="swept",
        )
        await db.commit()
        run_id = run.id

    async def never_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        raise AssertionError("executor must not run for a terminal run")
        if False:  # pragma: no cover - async generator 형태 유지
            yield ""

    user = CurrentUser(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        is_super_user=True,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="terminal-guard-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(Any, object()),
        user=user,
        input_payload={"content": "x"},
        moldy_source="chat",
        executor_fn=never_stream,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    async with TestSession() as db:
        refreshed = await db.get(ConversationRun, run_id)
        assert refreshed is not None
        assert refreshed.status == "stale"
        assert refreshed.error_code == "stale_heartbeat"
