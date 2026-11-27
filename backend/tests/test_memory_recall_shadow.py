"""The live comparison observes the selected lists without affecting the chat run."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import ORG_INFO_KEY, SCOPE_INFO_KEY
from app.models.job import Job
from app.models.memory import MemoryRecall, MemoryRecallRun
from app.services import memory_embedder, memory_recall, memory_recall_shadow
from app.services.memory_recall import Filters
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.sqlite_test_engine import TestSession as UnitSession
from tests.test_memory_recall import _count, _embed, _run, _save
from tests.test_memory_service import _seed_agent

pytestmark = pytest.mark.asyncio
CONTEXT = unit_context(TEST_USER_ID)
QUESTION = "포장 규칙"
KEYWORD_FACT = "포장 규칙은 파란 상자를 쓴다"


@pytest.fixture
def approved() -> memory_embedder.OpenAIShapedEmbedder:
    return memory_embedder.OpenAIShapedEmbedder(
        model="shadow-check",
        provider="openai_compatible",
        registry_id=uuid.uuid4(),
        base_url="https://embedding.invalid/v1",
        api_key="test-only-shadow-key",
        dimensions=3,
        generation=1,
        recall_mode="shadow",
        recall_floor=0.5,
    )


def _observations(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        vars(record) for record in caplog.records if record.name == memory_recall_shadow.__name__
    ]


async def _drain_shadow() -> None:
    await asyncio.gather(*tuple(memory_recall_shadow._tasks))


async def test_shadow_returns_keywords_before_provider_completes_and_only_logs_selected_counts(
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    keyword = await _save(db, KEYWORD_FACT)
    model_key = memory_embedder.vector_model_key(approved)
    await _embed(db, keyword, [1.0, 0.0, 0.0], model_key)
    for content in ["밀봉 지침", "완충재 메모", "송장 부착", "봉투 선택", "라벨 색상"]:
        record = await _save(db, content)
        await _embed(db, record, [0.9, 0.1, 0.0], model_key)
    excluded = await _save(db, "송장 아래에 붙인다", scope="agent", agent_id=agent_id)
    await _embed(db, excluded, [1.0, 0.0, 0.0], model_key)
    jobs_before = await _count(db, Job)
    run = await _run(db, conversation_id, agent_id, QUESTION)
    filters = Filters(scopes="both", agent_id=agent_id, exclude=frozenset({excluded.id}))

    resolver = AsyncMock(return_value=approved)
    monkeypatch.setattr(memory_embedder, "resolve_embedder", resolver)
    started, release = asyncio.Event(), asyncio.Event()
    calls: list[list[str]] = []

    async def blocked_embedding(
        self: memory_embedder.OpenAIShapedEmbedder, texts: list[str], *, seconds: float
    ) -> list[np.ndarray]:
        calls.append(texts)
        started.set()
        await release.wait()
        return [memory_embedder.normalized([1, 0, 0])]

    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", blocked_embedding)
    sessions: list[AsyncSession] = []

    def session_factory() -> AsyncSession:
        session = UnitSession()
        sessions.append(session)
        return session

    caplog.set_level(logging.INFO, logger=memory_recall_shadow.__name__)
    try:
        result = await memory_recall.timed_recall_for_run(
            session_factory, CONTEXT, run_id=str(run.id), filters=filters
        )
        assert result.mode == "fresh" and [row.id for row in result.records] == [keyword.id]
        assert KEYWORD_FACT in result.block
        await asyncio.wait_for(started.wait(), timeout=3)
        assert not sessions[1].in_transaction()
    finally:
        release.set()
        await _drain_shadow()

    assert calls == [[QUESTION]]
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert sessions[1].info[SCOPE_INFO_KEY] == {
        "app.tenant_id": str(TEST_TENANT_ID),
        "app.user_id": str(TEST_USER_ID),
    }
    assert sessions[1].info[ORG_INFO_KEY] == TEST_ORG_ID
    [event] = _observations(caplog)
    assert event["status"] == "completed"
    # Six readable user candidates reach ranking; only four fit the per-scope cap.
    assert (event["keyword_count"], event["hybrid_count"], event["overlap_count"]) == (1, 4, 1)
    assert event["keyword_tokens"] == memory_recall.estimate_tokens(result.block)
    assert event["hybrid_tokens"] > event["keyword_tokens"]
    assert event["added_tokens"] == event["hybrid_tokens"] - event["keyword_tokens"]
    assert event["latency_ms"] >= 0 and event["latency_p95_ms"] >= 0
    assert event["latency_window_count"] >= 1
    for private_value in (QUESTION, KEYWORD_FACT, str(keyword.id), str(run.id), approved.api_key):
        assert private_value and private_value not in str(event)
    assert list(await db.scalars(select(MemoryRecall.record_id))) == [keyword.id]
    assert await _count(db, MemoryRecallRun) == 1 and await _count(db, Job) == jobs_before


@pytest.mark.parametrize("change", ["disabled", "endpoint", "registry", "floor", "serve"])
async def test_shadow_rechecks_policy_before_sending_query(
    change: str,
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    keyword = await _save(db, KEYWORD_FACT)
    await _embed(db, keyword, [1.0, 0.0, 0.0], memory_embedder.vector_model_key(approved))
    run = await _run(db, conversation_id, agent_id, QUESTION)
    after = {
        "disabled": None,
        "endpoint": replace(approved, base_url="https://changed.invalid/v1"),
        "registry": replace(approved, registry_id=uuid.uuid4()),
        "floor": replace(approved, recall_floor=0.8),
        "serve": replace(approved, recall_mode="serve"),
    }[change]
    resolver = AsyncMock(side_effect=[approved, after])
    monkeypatch.setattr(memory_embedder, "resolve_embedder", resolver)
    embed = AsyncMock()
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    caplog.set_level(logging.INFO, logger=memory_recall_shadow.__name__)

    result = await memory_recall.timed_recall_for_run(
        UnitSession, CONTEXT, run_id=str(run.id), filters=Filters("both", agent_id)
    )
    await _drain_shadow()

    assert [row.id for row in result.records] == [keyword.id]
    assert resolver.await_count == 2
    embed.assert_not_awaited()
    assert [event["status"] for event in _observations(caplog)] == ["policy_changed"]


@pytest.mark.parametrize("failure", ["provider", "database", "incomplete_index", "busy", "timeout"])
async def test_shadow_outage_keeps_keyword_result_and_records(
    failure: str,
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    keyword = await _save(db, KEYWORD_FACT)
    await _embed(db, keyword, [1.0, 0.0, 0.0], memory_embedder.vector_model_key(approved))
    run = await _run(db, conversation_id, agent_id, QUESTION)
    monkeypatch.setattr(memory_embedder, "resolve_embedder", AsyncMock(return_value=approved))
    if failure == "incomplete_index":
        await _save(db, "아직 색인하지 않은 방수 테이프")
    jobs_before = await _count(db, Job)
    embed = AsyncMock(return_value=[memory_embedder.normalized([1.0, 0.0, 0.0])])
    if failure == "provider":
        embed.side_effect = OSError(QUESTION)
    elif failure == "busy":
        monkeypatch.setattr(memory_recall_shadow, "_MAX_TASKS", 0)
    elif failure == "timeout":

        async def never_finishes(*args, **kwargs) -> None:
            await asyncio.Event().wait()

        embed.side_effect = never_finishes
        monkeypatch.setattr(memory_recall_shadow.settings, "memory_embedding_timeout_seconds", 0.01)
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    sessions_opened = 0

    def session_factory() -> AsyncSession:
        nonlocal sessions_opened
        sessions_opened += 1
        if failure == "database" and sessions_opened == 2:
            raise OSError(KEYWORD_FACT)
        return UnitSession()

    caplog.set_level(logging.INFO, logger=memory_recall_shadow.__name__)
    result = await memory_recall.timed_recall_for_run(
        session_factory, CONTEXT, run_id=str(run.id), filters=Filters("both", agent_id)
    )
    await _drain_shadow()

    assert [row.id for row in result.records] == [keyword.id]
    assert list(await db.scalars(select(MemoryRecall.record_id))) == [keyword.id]
    assert await _count(db, MemoryRecallRun) == 1 and await _count(db, Job) == jobs_before
    [event] = _observations(caplog)
    assert event["status"] == (failure if failure in {"busy", "timeout"} else "unavailable")
    if failure == "busy":
        embed.assert_not_awaited()
        assert sessions_opened == 1
    assert not memory_recall_shadow._tasks
    assert QUESTION not in str(event) and KEYWORD_FACT not in str(event)


async def test_shadow_only_runs_for_fresh_keyword_selections(
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    keyword = await _save(db, KEYWORD_FACT)
    filters = Filters("both", agent_id)
    start = await _run(db, conversation_id, agent_id, QUESTION)
    disabled = replace(approved, recall_mode="off", recall_floor=None)
    monkeypatch.setattr(memory_embedder, "resolve_embedder", AsyncMock(return_value=disabled))
    embed = AsyncMock()
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    caplog.set_level(logging.INFO, logger=memory_recall_shadow.__name__)
    result = await memory_recall.timed_recall_for_run(
        UnitSession, CONTEXT, run_id=str(start.id), filters=filters
    )
    assert result.mode == "fresh" and [row.id for row in result.records] == [keyword.id]

    monkeypatch.setattr(memory_embedder, "resolve_embedder", AsyncMock(return_value=approved))
    for message, mode in [(QUESTION, "reused"), ("계속", "reference"), ("안녕", "skipped")]:
        run = start if mode == "reused" else await _run(db, conversation_id, agent_id, message)
        result = await memory_recall.timed_recall_for_run(
            UnitSession, CONTEXT, run_id=str(run.id), filters=filters
        )
        assert result.mode == mode
    await _drain_shadow()
    embed.assert_not_awaited()
    assert not _observations(caplog)


@pytest.mark.parametrize("failure", ["model_lookup", "scheduling"])
async def test_optional_shadow_setup_failure_after_or_before_selection_is_fail_open(
    failure: str,
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    keyword = await _save(db, KEYWORD_FACT)
    run = await _run(db, conversation_id, agent_id, QUESTION)
    resolver = AsyncMock(return_value=approved)
    if failure == "model_lookup":
        resolver.side_effect = RuntimeError("lookup unavailable")
    monkeypatch.setattr(memory_embedder, "resolve_embedder", resolver)
    schedule = Mock(side_effect=RuntimeError("schedule unavailable"))
    monkeypatch.setattr(memory_recall_shadow, "schedule", schedule)

    result = await memory_recall.timed_recall_for_run(
        UnitSession, CONTEXT, run_id=str(run.id), filters=Filters("both", agent_id)
    )

    assert result.mode == "fresh" and [row.id for row in result.records] == [keyword.id]
    assert await _count(db, MemoryRecallRun) == 1
    assert list(await db.scalars(select(MemoryRecall.record_id))) == [keyword.id]
    assert schedule.call_count == int(failure == "scheduling")


async def test_latency_windows_are_isolated_by_trial_and_outages_keep_the_same_trial_id(
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    keyword = await _save(db, KEYWORD_FACT)
    await _embed(db, keyword, [1.0, 0.0, 0.0], memory_embedder.vector_model_key(approved))
    resolver = AsyncMock()
    monkeypatch.setattr(memory_embedder, "resolve_embedder", resolver)
    embed = AsyncMock(return_value=[memory_embedder.normalized([1, 0, 0])])
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    caplog.set_level(logging.INFO, logger=memory_recall_shadow.__name__)
    for generation, failure in [(1, None), (2, None), (1, None), (2, "provider"), (1, "busy")]:
        resolver.return_value = replace(approved, generation=generation)
        if failure == "provider":
            embed.side_effect = OSError("provider unavailable")
        elif failure == "busy":
            monkeypatch.setattr(memory_recall_shadow, "_MAX_TASKS", 0)
        run = await _run(db, conversation_id, agent_id, QUESTION)
        result = await memory_recall.timed_recall_for_run(
            UnitSession, CONTEXT, run_id=str(run.id), filters=Filters("both", agent_id)
        )
        assert [row.id for row in result.records] == [keyword.id]
        await _drain_shadow()

    first, second, repeated, failed, busy = _observations(caplog)
    assert first["trial_id"] == repeated["trial_id"] == busy["trial_id"]
    assert second["trial_id"] == failed["trial_id"] != first["trial_id"]
    assert [event["status"] for event in (first, second, repeated, failed, busy)] == [
        "completed",
        "completed",
        "completed",
        "unavailable",
        "busy",
    ]
    assert [event["latency_window_count"] for event in (first, second, repeated)] == [1, 1, 2]
