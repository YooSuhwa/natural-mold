from __future__ import annotations

import os
import uuid

import anyio
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_queue_service, conversation_run_service

pytestmark = pytest.mark.integration


def _postgres_session_factory() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    raw_url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not raw_url:
        pytest.skip("INTEGRATION_DATABASE_URL is required")
    parsed = make_url(raw_url)
    if not (parsed.database or "").startswith("moldy_pg_lane_"):
        pytest.fail("queue concurrency requires a disposable PostgreSQL lane")
    engine = create_async_engine(parsed.set(drivername="postgresql+asyncpg"))
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _seed_conversation(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    model_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"queue-{user_id.hex}@test.local",
                name="Queue Owner",
                hashed_password="h",
            )
        )
        db.add(
            Model(
                id=model_id,
                provider="openai",
                model_name=f"queue-{model_id.hex}",
                display_name="Queue Model",
            )
        )
        db.add(
            Agent(
                id=agent_id,
                user_id=user_id,
                name="Queue Agent",
                system_prompt="Keep order.",
                model_id=model_id,
            )
        )
        db.add(Conversation(id=conversation_id, agent_id=agent_id, title="Queue"))
        await db.commit()
    return user_id, agent_id, conversation_id


async def _enqueue(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request_id: str,
) -> uuid.UUID:
    async with session_factory() as db:
        queued = await conversation_run_queue_service.enqueue_input(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
            client_request_id=request_id,
            source="chat",
            input_payload={"messages": [{"role": "user", "content": request_id}]},
            attachment_ids=[],
            checkpoint_id=None,
            priority=0,
        )
        await db.commit()
        return queued.id


@pytest.mark.asyncio
async def test_two_concurrent_claims_bind_exactly_one_active_run() -> None:
    # Given one pending input in an isolated PostgreSQL conversation.
    session_factory, engine = _postgres_session_factory()
    user_id, _agent_id, conversation_id = await _seed_conversation(session_factory)
    queued_id = await _enqueue(
        session_factory,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id="claim-race",
    )
    results: list[uuid.UUID | None] = []

    async def claim() -> None:
        async with session_factory() as db:
            claimed = await conversation_run_queue_service.claim_next_input(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            await db.commit()
            results.append(claimed.input.id if claimed is not None else None)

    # When two workers claim concurrently.
    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(claim)
            task_group.start_soon(claim)

        # Then one claim wins and the one-active-run index remains satisfied.
        assert sorted(item is not None for item in results) == [False, True]
        async with session_factory() as db:
            active_count = await db.scalar(
                select(func.count())
                .select_from(ConversationRun)
                .where(
                    ConversationRun.conversation_id == conversation_id,
                    ConversationRun.is_active.is_(True),
                )
            )
            queued = await db.get(ConversationRunInput, queued_id)
        assert active_count == 1
        assert queued is not None and queued.run_id is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_duplicate_request_ids_return_one_input() -> None:
    # Given two workers retrying the same client request id.
    session_factory, engine = _postgres_session_factory()
    user_id, _agent_id, conversation_id = await _seed_conversation(session_factory)
    results: list[uuid.UUID] = []

    async def enqueue_retry() -> None:
        results.append(
            await _enqueue(
                session_factory,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id="same-request",
            )
        )

    # When both transactions persist concurrently.
    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(enqueue_retry)
            task_group.start_soon(enqueue_retry)

        # Then conversation locking and the unique constraint produce one identity.
        assert len(set(results)) == 1
        async with session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(ConversationRunInput)
                .where(
                    ConversationRunInput.conversation_id == conversation_id,
                    ConversationRunInput.client_request_id == "same-request",
                )
            )
        assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stop_and_claim_share_conversation_first_lock_order() -> None:
    # Given an active predecessor plus a pending follow-up.
    session_factory, engine = _postgres_session_factory()
    user_id, agent_id, conversation_id = await _seed_conversation(session_factory)
    await _enqueue(
        session_factory,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id="after-stop",
    )
    async with session_factory() as db:
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_id=user_id,
            source="chat",
            input_preview="active",
        )
        await conversation_run_service.transition_run(db, run, "running")
        await db.commit()
        run_id = run.id
    claims: list[bool] = []

    async def stop() -> None:
        async with session_factory() as db:
            await conversation_run_queue_service.pause_queue(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            locked = await conversation_run_service.get_run_for_user(
                db,
                conversation_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                for_update=True,
            )
            assert locked is not None
            await conversation_run_service.request_cancel_run(db, locked, reason="stop")
            await db.commit()

    async def claim() -> None:
        async with session_factory() as db:
            result = await conversation_run_queue_service.claim_next_input(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            await db.commit()
            claims.append(result is not None)

    # When stop and claim race under PostgreSQL row locks.
    try:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(stop)
                task_group.start_soon(claim)

        # Then neither deadlocks nor starts overlapping graph state.
        assert claims == [False]
        async with session_factory() as db:
            conversation = await db.get(Conversation, conversation_id)
            active = await db.get(ConversationRun, run_id)
            pending_count = await db.scalar(
                select(func.count())
                .select_from(ConversationRunInput)
                .where(
                    ConversationRunInput.conversation_id == conversation_id,
                    ConversationRunInput.status == "pending",
                )
            )
        assert conversation is not None and conversation.queue_paused is True
        assert active is not None and active.status == "canceling"
        assert pending_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_stop_steer_and_claim_do_not_deadlock_or_overlap() -> None:
    # Given an active predecessor and a normal pending follow-up.
    session_factory, engine = _postgres_session_factory()
    user_id, agent_id, conversation_id = await _seed_conversation(session_factory)
    await _enqueue(
        session_factory,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id="normal-follow-up",
    )
    async with session_factory() as db:
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_id=user_id,
            source="chat",
            input_preview="active",
        )
        await conversation_run_service.transition_run(db, run, "running")
        await db.commit()
        run_id = run.id
    claims: list[bool] = []

    async def stop() -> None:
        async with session_factory() as db:
            await conversation_run_queue_service.pause_queue(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            locked = await conversation_run_service.get_run_for_user(
                db,
                conversation_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                for_update=True,
            )
            assert locked is not None
            await conversation_run_service.request_cancel_run(db, locked, reason="stop")
            await db.commit()

    async def steer() -> None:
        async with session_factory() as db:
            await conversation_run_queue_service.enqueue_input(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
                client_request_id="steer-correction",
                source="chat",
                input_payload={"messages": [{"role": "user", "content": "correct"}]},
                attachment_ids=[],
                checkpoint_id=None,
                priority=100,
            )
            locked = await conversation_run_service.get_run_for_user(
                db,
                conversation_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                for_update=True,
            )
            assert locked is not None
            await conversation_run_service.request_cancel_run(db, locked, reason="steer")
            await db.commit()

    async def claim() -> None:
        async with session_factory() as db:
            result = await conversation_run_queue_service.claim_next_input(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            await db.commit()
            claims.append(result is not None)

    # When stop, steer, and claim all contend on the same conversation.
    try:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(stop)
                task_group.start_soon(steer)
                task_group.start_soon(claim)

        # Then all operations finish and no second active run is allocated.
        assert claims == [False]
        async with session_factory() as db:
            active = await db.get(ConversationRun, run_id)
            active_count = await db.scalar(
                select(func.count())
                .select_from(ConversationRun)
                .where(
                    ConversationRun.conversation_id == conversation_id,
                    ConversationRun.is_active.is_(True),
                )
            )
            pending_request_ids = set(
                (
                    await db.scalars(
                        select(ConversationRunInput.client_request_id).where(
                            ConversationRunInput.conversation_id == conversation_id,
                            ConversationRunInput.status == "pending",
                        )
                    )
                ).all()
            )
        assert active is not None and active.status == "canceling"
        assert active_count == 1
        assert pending_request_ids == {"normal-follow-up", "steer-correction"}
    finally:
        await engine.dispose()
