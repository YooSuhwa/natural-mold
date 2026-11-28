"""The vector projection can score canonical memory, never grant access to it."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.job import Job
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.services import memory_embedder, memory_embedding_access, memory_recall
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_memory_recall import USER, _embed, _FakeEmbedder, _save, _seed_agent

QUERY = memory_recall.Query.of(["cobalt"])
CONTEXT = unit_context(TEST_USER_ID)
NOW = datetime(2026, 11, 27, 11)


async def _rank(db: AsyncSession, embedder: _FakeEmbedder | None) -> memory_recall.Ranked:
    return await memory_recall.rank(
        db,
        CONTEXT,
        QUERY,
        USER,
        embedder=embedder,
        candidate_floor=0.8 if embedder else None,
        now=NOW,
    )


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "old_content",
        "short_blob",
        "zero",
        "nan",
        "unnormalized",
        "foreign_org",
        "foreign_tenant",
        "foreign_user",
        "read_timeout",
    ],
)
async def test_one_invalid_projection_preserves_the_entire_keyword_order(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    await _seed_agent(db)
    lexical = await _save(db, "cobalt meeting")
    semantic = await _save(db, "blue pigment")
    embedder = _FakeEmbedder({"cobalt": [1.0, 0.0, 0.0]})
    await _embed(db, lexical, [0.0, 1.0, 0.0], embedder.model)
    await _embed(db, semantic, [1.0, 0.0, 0.0], embedder.model)
    good = await _rank(db, embedder)
    assert good.vector_used and {i.record.id for i in good.items} == {lexical.id, semantic.id}
    baseline = await _rank(db, None)
    assert [i.record.id for i in baseline.items] == [lexical.id]

    row = await db.scalar(select(MemoryEmbedding).where(MemoryEmbedding.record_id == lexical.id))
    assert row is not None
    if defect == "missing":
        await db.delete(row)
    elif defect == "old_content":
        row.text_hash = memory_embedder.text_hash("a previous revision")
    elif defect == "short_blob":
        row.vector = b"bad"
    elif defect == "foreign_org":
        row.org_id = uuid.uuid4()
    elif defect == "foreign_tenant":
        row.tenant_id = uuid.uuid4()
    elif defect == "foreign_user":
        row.user_id = uuid.uuid4()
    elif defect == "read_timeout":
        monkeypatch.setattr(memory_recall, "_vector_scores", AsyncMock(side_effect=TimeoutError))
    else:
        values = {"zero": [0, 0, 0], "nan": [np.nan, 0, 0], "unnormalized": [10, 0, 0]}
        row.vector = np.asarray(values[defect], dtype="<f4").tobytes()
    await db.commit()

    recalled = await _rank(db, embedder)
    assert recalled.items == baseline.items
    assert not recalled.vector_used
    assert recalled.fallback == (
        "index_unavailable" if defect == "read_timeout" else "index_incomplete"
    )
    assert embedder.calls == [["cobalt"], ["cobalt"]]
    assert not await db.scalar(select(Job.id).where(Job.idempotency_key.like("all:%")))


async def test_invalid_approval_and_sibling_owners_never_reach_recall(db: AsyncSession) -> None:
    await _seed_agent(db)
    now = datetime.now(UTC).replace(tzinfo=None)
    kept = []
    for i, changes in enumerate(
        [
            {},  # direct approval from the write path
            {"approval_channel": "proposal"},
            {"approval_channel": "auto_policy"},
            {
                "origin": "legacy",
                "approved_by": None,
                "approved_at": None,
                "approval_channel": None,
            },
            {"approved_by": uuid.uuid4()},
            {"approved_at": None},
            {"approval_channel": "pending"},
            {
                "origin": "user_stated",
                "approved_by": None,
                "approved_at": None,
                "approval_channel": None,
            },
            {"flags": {"legacy_rejected": True}},
            {"flags": {"legacy_rejected": "MEMORY_PII"}},
            {"revoked_at": now},
            {"deleted_at": now},
            {"status": "superseded"},
            {"org_id": uuid.uuid4()},
            {"tenant_id": uuid.uuid4()},
            {"user_id": uuid.uuid4()},
        ]
    ):
        record = await _save(db, f"cobalt meeting {i}")
        for key, value in changes.items():
            setattr(record, key, value)
        if i < 4:
            kept.append(record.id)
    await db.commit()
    ranked = await _rank(db, None)
    assert {i.record.id for i in ranked.items} == set(kept)


async def test_concurrent_edits_revoke_old_scores_and_keyword_tokens(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_agent(db)
    stable = await _save(db, "cobalt current")
    revoked = await _save(db, "cobalt revoked")
    deleted = await _save(db, "cobalt deleted")
    edited = await _save(db, "cobalt changed")
    approved = await _save(db, "cobalt formerly approved")
    moved = await _save(db, "cobalt moved")
    gained = await _save(db, "red ink")
    embedder = _FakeEmbedder({})
    for record in [stable, revoked, deleted, edited, approved, moved, gained]:
        await _embed(db, record, [1, 0, 0], embedder.model)

    async def change_during_embedding(texts: list[str], *, seconds: float) -> list[np.ndarray]:
        assert texts == ["cobalt"]
        async with TestSession() as writer:
            for record, values in [
                (revoked, {"status": "revoked"}),
                (approved, {"approved_at": None}),
                (moved, {"org_id": uuid.uuid4()}),
                (edited, {"content": "unrelated replacement"}),
                (gained, {"content": "cobalt replacement"}),
            ]:
                await writer.execute(
                    update(MemoryRecord).where(MemoryRecord.id == record.id).values(**values)
                )
            await writer.execute(delete(MemoryRecord).where(MemoryRecord.id == deleted.id))
            await writer.commit()
        return [memory_embedder.normalized([1, 0, 0])]

    monkeypatch.setattr(embedder, "embed", change_during_embedding)
    ranked = await _rank(db, embedder)
    assert ranked.fallback == "index_incomplete" and not ranked.vector_used
    assert {i.record.id for i in ranked.items} == {stable.id, gained.id}
    assert all(i.cosine is None for i in ranked.items)


async def test_stalled_provider_returns_keywords_inside_the_deadline(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_agent(db)
    record = await _save(db, "cobalt meeting")
    embedder = _FakeEmbedder({})

    async def stuck(texts: list[str], *, seconds: float) -> list[np.ndarray]:
        await asyncio.Event().wait()
        raise AssertionError("the deadline must cancel this request")

    monkeypatch.setattr(embedder, "embed", stuck)
    monkeypatch.setattr(settings, "memory_embedding_timeout_seconds", 0.05)
    ranked = await asyncio.wait_for(_rank(db, embedder), timeout=1.0)
    assert [i.record.id for i in ranked.items] == [record.id]
    assert ranked.fallback == "embedding_unavailable" and not ranked.vector_used


@pytest.mark.parametrize("bad_batch", [[], [np.array([1.0, 0.0, 0.0])], [np.zeros(3), np.zeros(3)]])
async def test_malformed_batch_uses_keyword_search_for_every_query(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, bad_batch: list[np.ndarray]
) -> None:
    await _seed_agent(db)
    first = await _save(db, "cobalt meeting")
    second = await _save(db, "ivory meeting")
    embedder = _FakeEmbedder({})
    embed = AsyncMock(return_value=bad_batch)
    monkeypatch.setattr(embedder, "embed", embed)
    monkeypatch.setattr(memory_embedder, "resolve_embedder", AsyncMock(return_value=embedder))
    monkeypatch.setitem(memory_recall.VECTOR_FLOORS, "fake-embed@3", 0.8)
    hits = await memory_recall.search(db, CONTEXT, queries=["cobalt", "ivory"], filters=USER)
    assert {hit.record.id for hit in hits} == {first.id, second.id}
    assert embed.await_count == 1


async def test_multiquery_search_drops_a_previous_hits_revision_and_approval(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_agent(db)
    edited = await _save(db, "cobalt first")
    revoked = await _save(db, "cobalt second")
    stable = await _save(db, "ivory meeting")
    monkeypatch.setattr(memory_embedder, "resolve_embedder", AsyncMock(return_value=None))
    rank = memory_recall.rank

    async def run_with_edit(*args: Any, **kwargs: Any) -> memory_recall.Ranked:
        if args[2].messages == ("ivory",):
            async with TestSession() as writer:
                await writer.execute(
                    update(MemoryRecord)
                    .where(MemoryRecord.id == edited.id)
                    .values(content="red ink")
                )
                await writer.execute(
                    update(MemoryRecord)
                    .where(MemoryRecord.id == revoked.id)
                    .values(approved_at=None)
                )
                await writer.commit()
        return await rank(*args, **kwargs)

    monkeypatch.setattr(memory_recall, "rank", run_with_edit)
    hits = await memory_recall.search(db, CONTEXT, queries=["cobalt", "ivory"], filters=USER)
    assert [hit.record.id for hit in hits] == [stable.id]


@pytest.mark.parametrize("barrier", ["shared_grant", "source_owner", "expiry"])
async def test_final_canonical_read_rechecks_grant_source_and_expiry(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, barrier: str
) -> None:
    agent, conversation = await _seed_agent(db)
    record = await _save(db, "cobalt context", scope="agent", agent_id=agent)
    if barrier == "shared_grant":
        await db.execute(update(Agent).where(Agent.id == agent).values(user_id=uuid.uuid4()))
        grants = AsyncMock(side_effect=[frozenset({agent}), frozenset()])
        monkeypatch.setattr(memory_recall, "shared_memory_agent_ids", grants)
    elif barrier == "source_owner":
        record.source_conversation_id = conversation
    else:
        record.valid_until = NOW + timedelta(milliseconds=100)
        ticks = iter([0.0, 1.0])
        monkeypatch.setattr(
            memory_recall,
            "time",
            SimpleNamespace(monotonic=lambda: next(ticks), perf_counter=lambda: 0.0),
        )
    await db.commit()
    embedder = _FakeEmbedder({})
    await _embed(db, record, [1, 0, 0], embedder.model)

    async def embed(texts: list[str], *, seconds: float) -> list[np.ndarray]:
        if barrier == "source_owner":
            async with TestSession() as writer:
                await writer.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation)
                    .values(user_id=uuid.uuid4())
                )
                await writer.commit()
        return [memory_embedder.normalized([1, 0, 0])]

    run_embedding = AsyncMock(side_effect=embed)
    monkeypatch.setattr(embedder, "embed", run_embedding)
    ranked = await memory_recall.rank(
        db,
        CONTEXT,
        QUERY,
        memory_recall.Filters("agent", agent),
        embedder=embedder,
        candidate_floor=0.8,
        now=NOW,
    )
    assert run_embedding.await_count == 1  # The first read admitted the candidate.
    assert ranked.items == []


@pytest.mark.parametrize(
    ("phase", "change"),
    [
        ("before", "denied"),
        ("after", "denied"),
        ("after", "generation"),
        ("after", "endpoint"),
        ("after", "floor"),
        ("after", "mode"),
    ],
)
async def test_serving_reauthorizes_after_analysis_and_provider(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, phase: str, change: str
) -> None:
    await _seed_agent(db)
    lexical = await _save(db, "cobalt notes")
    semantic = await _save(db, "blue pigment")
    lexical.search_tokens_analyzer = memory_recall.korean_terms.NORI_ANALYZER
    expected = memory_embedder.OpenAIShapedEmbedder(
        model="reds",
        dimensions=3,
        api_key="old-test-key",
        base_url="https://model.example",
        generation=1,
        registry_id=uuid.uuid4(),
        provider="litellm",
        recall_mode="serve",
        recall_floor=0.8,
    )
    fresh = replace(expected, api_key="fresh-test-key")
    model_key = memory_embedder.vector_model_key(expected)
    await _embed(db, lexical, [0, 1, 0], model_key)
    await _embed(db, semantic, [1, 0, 0], model_key)
    changes = {
        "denied": None,
        "generation": replace(fresh, generation=2),
        "endpoint": replace(fresh, base_url="https://changed.example"),
        "floor": replace(fresh, recall_floor=0.9),
        "mode": replace(fresh, recall_mode="off"),
    }
    resolutions = [changes[change]] if phase == "before" else [fresh, changes[change]]
    calls = []
    terms_with = memory_recall.korean_terms.terms_with

    async def grants(*args: object) -> frozenset[uuid.UUID]:
        calls.append("grant")
        return frozenset()

    async def terms(value: str, analyzer: str) -> memory_recall.korean_terms.Terms | None:
        if analyzer == memory_recall.korean_terms.NORI_ANALYZER:
            calls.append("analysis")
            return memory_recall.korean_terms.Terms(analyzer, ("cobalt",))
        return await terms_with(value, analyzer)

    async def authorize(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        org_id: uuid.UUID,
        model_id: uuid.UUID,
        *,
        generation: int,
    ) -> memory_embedder.OpenAIShapedEmbedder | None:
        assert (session, tenant_id, org_id, model_id, generation) == (
            db,
            CONTEXT.tenant_id,
            CONTEXT.org_id,
            expected.registry_id,
            1,
        )
        calls.append("authorize")
        return resolutions.pop(0)

    async def embed(
        self: memory_embedder.OpenAIShapedEmbedder, texts: list[str], *, seconds: float
    ) -> list[np.ndarray]:
        assert self.api_key == fresh.api_key and texts == ["cobalt"]
        calls.append("embed")
        return [memory_embedder.normalized([1, 0, 0])]

    monkeypatch.setattr(memory_recall.korean_terms, "terms_with", terms)
    monkeypatch.setattr(memory_recall, "shared_memory_agent_ids", grants)
    monkeypatch.setattr(memory_embedding_access, "org_embedder_for", authorize)
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    ranked = await memory_recall.rank(db, CONTEXT, QUERY, USER, embedder=expected)
    assert [hit.record.id for hit in ranked.items] == [lexical.id]
    assert not ranked.vector_used and ranked.fallback == "policy_changed"
    assert calls == (
        ["grant", "analysis", "authorize", "grant"]
        if phase == "before"
        else ["grant", "analysis", "authorize", "embed", "grant", "authorize"]
    )


async def test_extra_provider_vector_cannot_supplement_the_query(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_agent(db)
    lexical = await _save(db, "cobalt meeting")
    semantic = await _save(db, "blue pigment")
    embedder = _FakeEmbedder({})
    for record in (lexical, semantic):
        await _embed(db, record, [1, 0, 0], embedder.model)
    supplied = [memory_embedder.normalized(values) for values in ([0, 1, 0], [1, 0, 0])]
    monkeypatch.setattr(embedder, "embed", AsyncMock(return_value=supplied))
    result = await _rank(db, embedder)
    assert [item.record.id for item in result.items] == [lexical.id]
    assert result.fallback == "embedding_unavailable"
