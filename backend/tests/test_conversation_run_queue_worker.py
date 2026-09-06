from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.user import User
from app.services import (
    conversation_run_queue_service,
    conversation_run_queue_worker,
    conversation_run_service,
    conversation_run_worker,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
async def test_owner_worker_polls_durable_steer_and_acknowledges_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a running worker whose executor is blocked in model work.
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="active",
        )
        await db.commit()
        run_id = run.id

    entered = anyio.Event()

    async def blocked_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        entered.set()
        await anyio.sleep_forever()
        if False:
            yield ""

    monkeypatch.setattr(conversation_run_worker, "_heartbeat_interval_seconds", lambda: 0.01)
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="owning-worker")
    conversation_run_worker.reset_run_task_registry_for_tests(registry)
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(AgentConfig, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "active"}]},
        moldy_source="chat",
        executor_fn=blocked_executor,
        registry=registry,
    )
    with anyio.fail_after(2):
        await entered.wait()

    # When another process persists steer cancellation intent without local task access.
    async with TestSession() as db:
        active = await db.get(ConversationRun, run_id, with_for_update=True)
        assert active is not None
        await conversation_run_service.request_cancel_run(db, active, reason="steer")
        await db.commit()
    task = registry.get(run_id)
    assert task is not None
    await task

    # Then the owning worker observes DB intent and writes terminal acknowledgement.
    async with TestSession() as db:
        terminal = await db.get(ConversationRun, run_id)
        assert terminal is not None
        assert terminal.status == "canceled"
        assert terminal.cancel_reason == "steer"
        assert terminal.cancellation_acknowledged_at is not None


@pytest.mark.asyncio
async def test_duplicate_nonowner_does_not_acknowledge_owned_canceling_run() -> None:
    # Given a canceling run that another worker already owns.
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="owned",
        )
        await conversation_run_service.transition_run(
            db,
            run,
            "running",
            worker_instance_id="worker-a",
        )
        await conversation_run_service.request_cancel_run(db, run, reason="steer")
        await db.commit()
        run_id = run.id
    executor_called = False

    async def should_not_execute(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        nonlocal executor_called
        executor_called = True
        if False:
            yield ""

    duplicate_registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="worker-b")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cast(AgentConfig, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": []},
        moldy_source="chat",
        executor_fn=should_not_execute,
        registry=duplicate_registry,
    )
    duplicate_task = duplicate_registry.get(run_id)
    assert duplicate_task is not None
    await duplicate_task

    # Then the duplicate exits without pretending the owning worker acknowledged.
    async with TestSession() as db:
        durable = await db.get(ConversationRun, run_id)
    assert executor_called is False
    assert durable is not None and durable.status == "canceling"
    assert durable.worker_instance_id == "worker-a"
    assert durable.cancellation_acknowledged_at is None


@pytest.mark.asyncio
async def test_repeated_steer_inputs_dispatch_in_persisted_order(db: AsyncSession) -> None:
    # Given two corrections accepted while one predecessor remains active.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    active = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="active",
    )
    await conversation_run_service.transition_run(
        db,
        active,
        "running",
        worker_instance_id="steer-owner",
    )
    first = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="steer-1",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "first correction"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=100,
    )
    second = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="steer-2",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "second correction"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=100,
    )
    await conversation_run_service.request_cancel_run(db, active, reason="steer")
    assert (
        await conversation_run_queue_service.claim_next_input(
            db, conversation_id=conversation_id, user_id=TEST_USER_ID
        )
        is None
    )

    # When the predecessor acknowledges cancellation and each correction completes.
    await conversation_run_service.transition_run(
        db,
        active,
        "canceled",
        cancellation_ack_worker_id="steer-owner",
    )
    first_claim = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, user_id=TEST_USER_ID
    )
    assert first_claim is not None
    await conversation_run_service.transition_run(db, first_claim.run, "failed")
    second_claim = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, user_id=TEST_USER_ID
    )

    # Then no correction overlaps and persisted FIFO breaks ties between steers.
    assert first_claim.input.id == first.id
    assert second_claim is not None
    assert second_claim.input.id == second.id


@pytest.mark.asyncio
async def test_recovery_launches_claim_committed_before_task_start(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a claimed run committed with no process-local task.
    conversation_id = await seed_conversation_with_agent()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="recover-claim",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "recover"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, user_id=TEST_USER_ID
    )
    assert claimed is not None
    await db.commit()
    launched: list[uuid.UUID] = []

    async def record_launch(input_id: uuid.UUID) -> ConversationRun | None:
        launched.append(input_id)
        return claimed.run

    monkeypatch.setattr(conversation_run_queue_worker, "launch_claimed_input", record_launch)

    # When periodic recovery scans durable state.
    count = await conversation_run_queue_worker.recover_conversation_queue()

    # Then it launches the exact committed input once.
    assert count == 1
    assert launched == [queued.id]


@pytest.mark.asyncio
async def test_recovery_terminalizes_claim_for_inactive_user(db: AsyncSession) -> None:
    # Given a claim committed before its user is deactivated.
    conversation_id = await seed_conversation_with_agent()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="inactive-owner",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "must not run"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, user_id=TEST_USER_ID
    )
    assert claimed is not None
    user = await db.get(User, TEST_USER_ID)
    assert user is not None
    user.is_active = False
    await db.commit()

    # When recovery rehydrates the stored identity.
    await conversation_run_queue_worker.launch_claimed_input(queued.id)

    # Then invalid identity cannot leave the one-active slot stuck.
    await db.refresh(claimed.run)
    failed_input = await db.get(ConversationRunInput, queued.id)
    assert failed_input is not None
    await db.refresh(failed_input)
    assert claimed.run.status == "failed"
    assert claimed.run.error_code == "queue_user_unavailable"
    assert claimed.run.is_active is False
    assert failed_input.status == "failed"


@pytest.mark.asyncio
async def test_stale_sweep_does_not_ack_or_dispatch_over_live_remote_owner(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a remotely owned canceling run, a correction, and an owner still executing.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    active = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="remote active",
    )
    await conversation_run_service.transition_run(
        db,
        active,
        "running",
        worker_instance_id="remote-owner",
    )
    active.heartbeat_at = active.created_at - timedelta(hours=1)
    await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="remote-steer",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "correction"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=100,
    )
    await conversation_run_service.request_cancel_run(db, active, reason="steer")
    await db.commit()
    launched: list[uuid.UUID] = []

    async def record_launch(input_id: uuid.UUID) -> ConversationRun | None:
        launched.append(input_id)
        return None

    monkeypatch.setattr(conversation_run_queue_worker, "launch_claimed_input", record_launch)
    conversation_run_worker.reset_run_task_registry_for_tests(
        conversation_run_worker.RunTaskRegistry(worker_instance_id="sweeper")
    )
    owner_alive = anyio.Event()
    release_owner = anyio.Event()

    async def remote_owner() -> None:
        owner_alive.set()
        await release_owner.wait()

    # When a non-owner stale sweep and recovery run, including after manual resume.
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(remote_owner)
        await owner_alive.wait()
        await conversation_run_service.sweep_stale_conversation_runs(session_factory=TestSession)
        first_recovery = await conversation_run_queue_worker.recover_conversation_queue()
        async with TestSession() as recovery_db:
            await conversation_run_queue_service.resume_queue(
                recovery_db,
                conversation_id=conversation_id,
                user_id=TEST_USER_ID,
            )
            await recovery_db.commit()
        second_recovery = await conversation_run_queue_worker.recover_conversation_queue()
        release_owner.set()

    # Then no non-owner path fabricates ACK or claims a concurrent successor.
    await db.refresh(active)
    await db.refresh(conversation)
    queued = await db.scalar(
        select(ConversationRunInput).where(ConversationRunInput.client_request_id == "remote-steer")
    )
    assert active.status == "canceling"
    assert active.is_active is True
    assert active.cancellation_acknowledged_at is None
    assert queued is not None and queued.status == "pending" and queued.run_id is None
    assert first_recovery == 0 and second_recovery == 0
    assert launched == []


@pytest.mark.asyncio
async def test_startup_sweep_then_recovery_finishes_workerless_cancel_before_start(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an old workerless claimed run canceled before launch and a successor.
    conversation_id = await seed_conversation_with_agent()
    first = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="workerless-predecessor",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "first"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    claimed = await conversation_run_queue_service.claim_next_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
    )
    assert claimed is not None
    await conversation_run_service.request_cancel_run(db, claimed.run, reason="steer")
    claimed.run.created_at = claimed.run.cancel_requested_at - timedelta(hours=1)
    await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="workerless-successor",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "second"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=100,
    )
    await db.commit()
    successor_completed = anyio.Event()
    successor_payloads: list[dict[str, Any]] = []

    async def successor_executor(
        _cfg: AgentConfig,
        input_payload: dict[str, Any],
        **_kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        successor_payloads.append(input_payload)
        successor_completed.set()
        if False:
            yield ""

    monkeypatch.setattr(
        conversation_run_queue_worker,
        "execute_agent_stream_langgraph",
        successor_executor,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="startup-worker")
    conversation_run_worker.reset_run_task_registry_for_tests(registry)

    # When startup runs stale sweep before queue recovery.
    await conversation_run_service.sweep_stale_conversation_runs(session_factory=TestSession)
    await db.refresh(claimed.run)
    assert claimed.run.status == "canceling"
    recovered = await conversation_run_queue_worker.recover_conversation_queue()
    with anyio.fail_after(3):
        await successor_completed.wait()
    successor_ids = registry.active_run_ids()
    for run_id in successor_ids:
        task = registry.get(run_id)
        if task is not None:
            await task

    # Then the proven workerless finalizer ACKs and exactly one successor runs.
    await db.refresh(claimed.run)
    await db.refresh(first)
    successor = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.client_request_id == "workerless-successor"
        )
    )
    assert recovered == 1
    assert claimed.run.status == "canceled"
    assert claimed.run.cancellation_acknowledged_at is not None
    assert first.status == "canceled"
    assert first.run_id is None
    assert successor is not None and successor.run_id is not None
    assert successor_payloads == [{"messages": [{"role": "user", "content": "second"}]}]


@pytest.mark.asyncio
async def test_stale_remote_owner_with_ordinary_queue_pauses_automatic_dispatch(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an old remotely owned run with no cancel intent and a normal queued input.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    active = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="remote ordinary",
    )
    await conversation_run_service.transition_run(
        db,
        active,
        "running",
        worker_instance_id="remote-owner",
    )
    active.heartbeat_at = active.created_at - timedelta(hours=1)
    await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="ordinary-after-remote",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "later"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    launched: list[uuid.UUID] = []

    async def record_launch(input_id: uuid.UUID) -> ConversationRun | None:
        launched.append(input_id)
        return None

    monkeypatch.setattr(conversation_run_queue_worker, "launch_claimed_input", record_launch)
    conversation_run_worker.reset_run_task_registry_for_tests(
        conversation_run_worker.RunTaskRegistry(worker_instance_id="sweeper")
    )

    # When heartbeat-based stale sweep runs while the remote executor may still be alive.
    await conversation_run_service.sweep_stale_conversation_runs(session_factory=TestSession)
    recovered = await conversation_run_queue_worker.recover_conversation_queue()

    # Then legacy stale status may advance, but automatic queue dispatch remains paused.
    await db.refresh(active)
    await db.refresh(conversation)
    assert active.status == "stale"
    assert active.cancellation_acknowledged_at is None
    assert conversation.queue_paused is True
    assert recovered == 0
    assert launched == []


@pytest.mark.asyncio
async def test_startup_recovers_workerless_direct_cancel_then_dispatches_once(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an old direct/reject run canceled before worker start and a paused successor.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    direct = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="direct before crash",
    )
    await conversation_run_service.request_cancel_run(db, direct, reason="stop")
    direct.created_at = direct.cancel_requested_at - timedelta(hours=1)
    await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="after-direct-cancel",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "continue"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await conversation_run_queue_service.pause_queue(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
    )
    await db.commit()
    successor_completed = anyio.Event()
    successor_payloads: list[dict[str, Any]] = []

    async def successor_executor(
        _cfg: AgentConfig,
        input_payload: dict[str, Any],
        **_kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        successor_payloads.append(input_payload)
        successor_completed.set()
        if False:
            yield ""

    monkeypatch.setattr(
        conversation_run_queue_worker,
        "execute_agent_stream_langgraph",
        successor_executor,
    )
    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="startup-worker")
    conversation_run_worker.reset_run_task_registry_for_tests(registry)

    # When startup sweeps then recovers, followed by the user's explicit resume.
    await conversation_run_service.sweep_stale_conversation_runs(session_factory=TestSession)
    await db.refresh(direct)
    assert direct.status == "canceling"
    first_recovery = await conversation_run_queue_worker.recover_conversation_queue()
    async with TestSession() as recovery_db:
        await conversation_run_queue_service.resume_queue(
            recovery_db,
            conversation_id=conversation_id,
            user_id=TEST_USER_ID,
        )
        await recovery_db.commit()
    second_recovery = await conversation_run_queue_worker.recover_conversation_queue()
    with anyio.fail_after(3):
        await successor_completed.wait()
    for run_id in registry.active_run_ids():
        task = registry.get(run_id)
        if task is not None:
            await task

    # Then workerless proof ACKs the direct run and one successor executes.
    await db.refresh(direct)
    successor = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.client_request_id == "after-direct-cancel"
        )
    )
    assert first_recovery == 0
    assert second_recovery == 1
    assert direct.status == "canceled"
    assert direct.cancellation_acknowledged_at is not None
    assert successor is not None and successor.run_id is not None
    assert successor_payloads == [{"messages": [{"role": "user", "content": "continue"}]}]
