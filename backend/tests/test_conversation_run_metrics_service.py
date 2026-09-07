from __future__ import annotations

import uuid
from dataclasses import replace
from typing import cast

import pytest
from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent_runtime.run_metrics import RunMetricActivity, RunMetricsSnapshot
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.models.token_usage import TokenUsage
from app.services import conversation_run_service
from app.services.conversation_run_metrics_service import (
    persist_run_metrics,
    persist_run_metrics_and_usage,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


def _snapshot() -> RunMetricsSnapshot:
    return RunMetricsSnapshot(
        terminal_state="canceled",
        elapsed_ms=4_000.0,
        ttft_ms=1_000.0,
        generation_ms=2_000.0,
        tokens_per_second=2.0,
        prompt_tokens=8,
        completion_tokens=4,
        cache_creation_tokens=1,
        cache_read_tokens=2,
        estimated_cost=0.12,
        usage_complete=False,
        root_tool_calls=1,
        descendant_tool_calls=2,
        root_subagent_calls=0,
        descendant_subagent_calls=1,
        activity=(
            RunMetricActivity(
                kind="tool_call",
                namespace=(),
                call_id="tool-1",
                name="search",
                elapsed_ms=1_500.0,
            ),
        ),
        activity_truncated=False,
    )


CONVERSATION_RUN_TABLE = cast(Table, ConversationRun.__table__)
CONVERSATION_RUN_METRICS_TABLE = cast(Table, ConversationRunMetrics.__table__)


@pytest.mark.asyncio
async def test_persisted_metrics_reload_with_run_association(tmp_path) -> None:
    # Given: isolated storage with only the explicitly imported run metrics model.
    run_id = uuid.uuid4()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: CONVERSATION_RUN_TABLE.create(sync_connection)
        )
        await connection.run_sync(
            lambda sync_connection: CONVERSATION_RUN_METRICS_TABLE.create(sync_connection)
        )

    # When: a terminal partial snapshot is persisted and loaded from a fresh session.
    async with session_factory() as session:
        await persist_run_metrics(session, run_id=run_id, snapshot=_snapshot())
        await session.commit()
    async with session_factory() as session:
        restored = await session.get(ConversationRunMetrics, run_id)

    # Then: measured values, completeness, activity, and parent association survive reload.
    assert restored is not None
    assert restored.run_id == run_id
    assert restored.terminal_state == "canceled"
    assert restored.prompt_tokens == 8
    assert restored.completion_tokens == 4
    assert restored.usage_complete is False
    assert restored.activity_json == [
        {
            "kind": "tool_call",
            "namespace": [],
            "call_id": "tool-1",
            "name": "search",
            "elapsed_ms": 1_500.0,
        }
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_finalizer_retry_keeps_exactly_one_metrics_row(tmp_path) -> None:
    # Given: an isolated metrics table and one frozen terminal snapshot.
    run_id = uuid.uuid4()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: CONVERSATION_RUN_TABLE.create(sync_connection)
        )
        await connection.run_sync(
            lambda sync_connection: CONVERSATION_RUN_METRICS_TABLE.create(sync_connection)
        )

    # When: a conflicting retry attempts to replace the frozen terminal truth.
    async with session_factory() as session:
        await persist_run_metrics(session, run_id=run_id, snapshot=_snapshot())
        await session.commit()
    async with session_factory() as session:
        await persist_run_metrics(
            session,
            run_id=run_id,
            snapshot=replace(
                _snapshot(),
                terminal_state="failed",
                completion_tokens=999,
            ),
        )
        await session.commit()

    # Then: the run owns one row and the first terminal snapshot remains immutable.
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(ConversationRunMetrics))
        restored = await session.get(ConversationRunMetrics, run_id)
    assert count == 1
    assert restored is not None
    assert restored.terminal_state == "canceled"
    assert restored.completion_tokens == 4
    await engine.dispose()


@pytest.mark.asyncio
async def test_unmeasured_values_persist_as_null_instead_of_zero(tmp_path) -> None:
    # Given: a failed pre-token run where usage and first-token measurements are unavailable.
    run_id = uuid.uuid4()
    snapshot = replace(
        _snapshot(),
        terminal_state="failed",
        ttft_ms=None,
        tokens_per_second=None,
        prompt_tokens=None,
        completion_tokens=None,
        cache_creation_tokens=None,
        cache_read_tokens=None,
        estimated_cost=None,
        usage_complete=False,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'unknown.db'}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: CONVERSATION_RUN_TABLE.create(sync_connection)
        )
        await connection.run_sync(
            lambda sync_connection: CONVERSATION_RUN_METRICS_TABLE.create(sync_connection)
        )

    # When: the incomplete snapshot is persisted and reloaded.
    async with session_factory() as session:
        await persist_run_metrics(session, run_id=run_id, snapshot=snapshot)
        await session.commit()
    async with session_factory() as session:
        restored = await session.get(ConversationRunMetrics, run_id)

    # Then: missing measurements remain unknown and measured generation remains available.
    assert restored is not None
    assert restored.prompt_tokens is None
    assert restored.completion_tokens is None
    assert restored.estimated_cost is None
    assert restored.ttft_ms is None
    assert restored.generation_ms == 2_000.0
    assert restored.usage_complete is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_run_retry_keeps_one_metrics_and_token_usage_row() -> None:
    conversation_id = await seed_conversation_with_agent(model_name="metrics-model")
    async with TestSession() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            session,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="metrics",
        )
        await persist_run_metrics_and_usage(
            session,
            run=run,
            snapshot=_snapshot(),
            model_name="metrics-model",
        )
        await session.commit()
        run_id = run.id

    async with TestSession() as session:
        run = await session.get(ConversationRun, run_id, with_for_update=True)
        assert run is not None
        await persist_run_metrics_and_usage(
            session,
            run=run,
            snapshot=replace(_snapshot(), completion_tokens=999),
            model_name="different-retry-model",
        )
        await session.commit()

    async with TestSession() as session:
        metrics_rows = (
            (
                await session.execute(
                    select(ConversationRunMetrics).where(ConversationRunMetrics.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        usage_rows = (
            (await session.execute(select(TokenUsage).where(TokenUsage.run_id == run_id)))
            .scalars()
            .all()
        )
    assert len(metrics_rows) == 1
    assert metrics_rows[0].completion_tokens == 4
    assert len(usage_rows) == 1
    assert usage_rows[0].completion_tokens == 4
    assert usage_rows[0].model_name == "metrics-model"


@pytest.mark.asyncio
async def test_unknown_first_snapshot_blocks_conflicting_retry_token_insert() -> None:
    conversation_id = await seed_conversation_with_agent(model_name="metrics-model")
    unknown = replace(
        _snapshot(),
        prompt_tokens=None,
        completion_tokens=None,
        cache_creation_tokens=None,
        cache_read_tokens=None,
        estimated_cost=None,
        usage_complete=False,
    )
    async with TestSession() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            session,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="unknown first",
        )
        await persist_run_metrics_and_usage(
            session,
            run=run,
            snapshot=unknown,
            model_name="metrics-model",
        )
        await session.commit()
        run_id = run.id

    async with TestSession() as session:
        run = await session.get(ConversationRun, run_id, with_for_update=True)
        assert run is not None
        await persist_run_metrics_and_usage(
            session,
            run=run,
            snapshot=_snapshot(),
            model_name="conflicting-known-retry",
        )
        await session.commit()

    async with TestSession() as session:
        restored = await session.get(ConversationRunMetrics, run_id)
        usage_count = await session.scalar(
            select(func.count()).select_from(TokenUsage).where(TokenUsage.run_id == run_id)
        )
    assert restored is not None
    assert restored.prompt_tokens is None
    assert restored.completion_tokens is None
    assert usage_count == 0
