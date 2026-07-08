from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.services import (
    chat_service,
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
        cfg=cast(Any, SimpleNamespace(secret_values=set())),
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
        cfg=cast(Any, SimpleNamespace(secret_values=set())),
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


@pytest.mark.asyncio
async def test_backfill_turn_attachments_stamps_orphan_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalize-side wiring (M1): resolve this turn's user message id and stamp
    the send's orphan uploads with it (own session, committed)."""

    conversation_id = await seed_conversation_with_agent()
    upload_id = uuid.uuid4()
    async with TestSession() as db:
        db.add(
            MessageAttachment(
                id=upload_id,
                user_id=TEST_USER_ID,
                conversation_id=conversation_id,
                message_id=None,
                filename="f.png",
                mime_type="image/png",
                size_bytes=3,
                storage_path=f"/tmp/{upload_id}.png",
                url=f"/api/uploads/{upload_id}",
            )
        )
        await db.commit()

    # No real checkpointer in unit tests — pin the resolved user message id.
    async def fake_resolve(_db: object, _conv: object, *, tree: object = None) -> str:
        return "resolved-user-msg-id"

    monkeypatch.setattr(chat_service, "resolve_turn_user_message_id", fake_resolve)

    await conversation_run_worker._backfill_turn_attachments(conversation_id, [upload_id])

    async with TestSession() as db:
        row = await db.get(MessageAttachment, upload_id)
        assert row is not None
        assert row.message_id == "resolved-user-msg-id"


@pytest.mark.asyncio
async def test_backfill_runs_after_branch_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 regression: in the finalize block, the attachment backfill must run
    AFTER branch activation. resolve_turn_user_message_id reads
    ``active_branch_checkpoint_id``; an edit/regenerate run forks a new leaf that
    is activated in the finalize block, so resolving before activation would walk
    the stale branch and mis-link. Guards against silently reverting the order
    (the helpers are correct in isolation; only their ordering here matters)."""

    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conv.agent_id,
            user_id=TEST_USER_ID,
            source="regenerate",
            input_preview="edit with attachment",
        )
        await db.commit()
        run_id = run.id

    call_order: list[str] = []

    async def fake_activate(**_kwargs: Any) -> None:
        call_order.append("activate")

    async def fake_backfill(_conv_id: object, _att_ids: object) -> None:
        call_order.append("backfill")

    monkeypatch.setattr(
        conversation_run_worker, "_activate_latest_branch_leaf_if_needed", fake_activate
    )
    monkeypatch.setattr(conversation_run_worker, "_backfill_turn_attachments", fake_backfill)

    async def empty_stream(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        if False:  # pragma: no cover - async generator 형태 유지
            yield ""

    user = CurrentUser(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        is_super_user=True,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="order-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(Any, SimpleNamespace(secret_values=set())),
        user=user,
        input_payload={"content": "x"},
        moldy_source="regenerate",
        executor_fn=empty_stream,
        registry=registry,
        attachment_ids=[uuid.uuid4()],
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    assert call_order == ["activate", "backfill"], call_order


@pytest.mark.asyncio
async def test_completed_regenerate_run_persists_latest_branch_leaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    monkeypatch.setattr(conversation_run_worker, "get_checkpointer", lambda: object())

    async def fake_build_message_tree(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(active_checkpoint_id="ck-new-leaf")

    monkeypatch.setattr(
        conversation_run_worker.thread_branch_service,
        "build_message_tree",
        fake_build_message_tree,
    )

    await conversation_run_worker._activate_latest_branch_leaf_if_needed(
        conversation_id=conversation_id,
        moldy_source="regenerate",
        final_status="completed",
    )

    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.active_branch_checkpoint_id == "ck-new-leaf"


@pytest.mark.asyncio
async def test_chat_run_does_not_persist_latest_branch_leaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await seed_conversation_with_agent()

    def fail_get_checkpointer() -> object:
        raise AssertionError("chat run must not inspect branch checkpoints")

    monkeypatch.setattr(conversation_run_worker, "get_checkpointer", fail_get_checkpointer)

    await conversation_run_worker._activate_latest_branch_leaf_if_needed(
        conversation_id=conversation_id,
        moldy_source="chat",
        final_status="completed",
    )

    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.active_branch_checkpoint_id is None


def test_redact_run_error_message_masks_injected_secret() -> None:
    # 예외 텍스트에 run credential 값이 echo되면 값 기반 마스킹으로 가려야 한다
    # (블록리스트가 놓치는 bare token/URL-embedded 케이스 방어 — CLAUDE.md redaction).
    secret = "sk-injected-secret-abcdef123456"
    masked = conversation_run_worker._redact_run_error_message(
        f"model call failed: token {secret} at https://gw/v1", {secret}
    )
    assert masked is not None
    assert secret not in masked


def test_redact_run_error_message_blank_returns_none() -> None:
    assert conversation_run_worker._redact_run_error_message("   ", set()) is None


@pytest.mark.asyncio
async def test_interrupted_run_transitions_before_trace_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M8-2 regression: 인터럽트 런은 상태 전이("interrupted" 커밋)가
    finalize_trace(느린 message_events 영속화)보다 먼저 실행되어야 한다.
    승인 카드는 스트림 도중 이미 클라이언트에 flush되므로, 전이가 trace 뒤로
    밀리면 그 사이 도착한 resume이 부모 run을 못 찾아 RESUME_NOT_FOUND로
    튕긴다. (헬퍼 각각은 올바르고 이 순서만이 계약이다 — backfill 순서 테스트와
    같은 방식.)"""

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
            input_preview="interrupt ordering",
        )
        await db.commit()
        run_id = run.id

    call_order: list[str] = []

    async def fake_finalize_trace(*_args: Any, **_kwargs: Any) -> None:
        call_order.append("finalize_trace")

    async def fake_transition(*_args: Any, **_kwargs: Any) -> None:
        call_order.append("transition")
        return None

    async def fake_activate(**_kwargs: Any) -> None:
        call_order.append("activate")

    monkeypatch.setattr(
        conversation_run_worker.stream_service, "finalize_trace", fake_finalize_trace
    )
    monkeypatch.setattr(conversation_run_worker, "_transition", fake_transition)
    monkeypatch.setattr(
        conversation_run_worker, "_activate_latest_branch_leaf_if_needed", fake_activate
    )
    monkeypatch.setattr(conversation_run_worker, "has_interrupt_events", lambda _sink: True)
    monkeypatch.setattr(
        conversation_run_worker, "interrupt_id_from_events", lambda _sink: "intr-order"
    )

    async def empty_stream(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        if False:  # pragma: no cover - async generator 형태 유지
            yield ""

    user = CurrentUser(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        is_super_user=True,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="intr-order-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(Any, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=user,
        input_payload={"content": "x"},
        moldy_source="chat",
        executor_fn=empty_stream,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    assert call_order == ["transition", "activate", "finalize_trace"], call_order


@pytest.mark.asyncio
async def test_completed_run_keeps_trace_before_transition_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대조군: 인터럽트가 아닌(완료) 런은 기존 순서 유지 — trace 완결 후 terminal
    전이. M8-2 재정렬이 completed/failed 경로를 건드리지 않았음을 고정한다."""

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
            input_preview="completed ordering",
        )
        await db.commit()
        run_id = run.id

    call_order: list[str] = []

    async def fake_finalize_trace(*_args: Any, **_kwargs: Any) -> None:
        call_order.append("finalize_trace")

    async def fake_transition(*_args: Any, **_kwargs: Any) -> None:
        call_order.append("transition")
        return None

    async def fake_activate(**_kwargs: Any) -> None:
        call_order.append("activate")

    monkeypatch.setattr(
        conversation_run_worker.stream_service, "finalize_trace", fake_finalize_trace
    )
    monkeypatch.setattr(conversation_run_worker, "_transition", fake_transition)
    monkeypatch.setattr(
        conversation_run_worker, "_activate_latest_branch_leaf_if_needed", fake_activate
    )

    async def empty_stream(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        if False:  # pragma: no cover - async generator 형태 유지
            yield ""

    user = CurrentUser(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        is_super_user=True,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="done-order-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(Any, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=user,
        input_payload={"content": "x"},
        moldy_source="chat",
        executor_fn=empty_stream,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    assert call_order == ["finalize_trace", "transition", "activate"], call_order
