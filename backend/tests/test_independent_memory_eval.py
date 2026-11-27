"""F09 quality reports use the selected list and the registered endpoint only."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import memory_embedder, model_service
from evals.memory_recall import independent
from tests.authz.unit_scope_fixture import TEST_ORG_ID
from tests.conftest import seed_agent
from tests.test_memory_vectors import _embedding, _tenant


def test_primary_metric_counts_distinct_selected_gold_and_empty_rejections() -> None:
    selected = {"inserted": ["a", "a", *[str(i) for i in range(8)], "b"]}
    assert independent._primary({"gold": ["a", "b"]}, selected) == 0.5
    assert independent._primary({"gold": []}, selected) == 0
    assert independent._primary({"gold": []}, {"inserted": []}) == 1


@pytest.mark.parametrize(
    ("rejection", "recall", "passed"),
    [
        (0.899, 0.60, False),
        (0.900, 0.50, False),
        (0.900, 0.501, True),
    ],
)
def test_gate_requires_both_metrics(rejection: float, recall: float, passed: bool) -> None:
    baseline = {"categories": {"paraphrase": {"value": 0.50}}}
    assert independent.verdict({"keyword": baseline})["passed"] is False
    hybrid = {"categories": {"paraphrase": {"value": recall}, "unrelated": {"value": rejection}}}
    assert independent.verdict({"keyword": baseline, "hybrid": hybrid})["passed"] is passed


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_wilson_interval_reports_uncertainty_at_boundaries(value: float) -> None:
    questions = [{"group": str(i)} for i in range(25)]
    estimate = independent._estimate(questions, [value] * 25, independent._mean, binary=True)
    lo, hi = estimate["ci95"]
    assert lo <= value <= hi
    assert hi - lo > 0.10
    assert estimate["n"] == 25


async def test_resolver_needs_the_exact_tenant_selection_and_binds_the_endpoint(
    db: AsyncSession,
) -> None:
    await seed_agent(db)
    tenant = await _tenant(db)
    model = await model_service.create_model(db, data=_embedding())
    with pytest.raises(ValueError, match="has not selected"):
        await independent.approved_candidate(db, tenant.id, TEST_ORG_ID, model.id, 0.42)
    tenant.settings = {"memory": {"embedding_model_id": str(model.id)}}
    await db.commit()
    candidate, provider = await independent.approved_candidate(
        db, tenant.id, TEST_ORG_ID, model.id, 0.42
    )
    assert (candidate.tenant_id, candidate.model_id) == (str(tenant.id), str(model.id))
    assert (candidate.provider, candidate.base_url) == (model.provider, provider.base_url)
    assert candidate.dimensions == model.embedding_dimensions
    with pytest.raises(ValueError, match="has not selected"):
        await independent.approved_candidate(db, uuid.uuid4(), TEST_ORG_ID, model.id, 0.42)
    with pytest.raises(ValueError, match="unavailable for this organization"):
        await independent.approved_candidate(db, tenant.id, uuid.uuid4(), model.id, 0.42)
    with pytest.raises(ValueError, match="cosine floor"):
        await independent.approved_candidate(db, tenant.id, TEST_ORG_ID, model.id, float("nan"))


async def test_evaluator_fails_the_hybrid_run_if_query_embedding_falls_back(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deterministic unit test only; this model never generates a report.
    embedder = memory_embedder.OpenAIShapedEmbedder("probe", 2, None, "https://example.invalid")
    provider = AsyncMock(
        side_effect=[
            [np.array([1.0, 0.0], dtype=np.float32)],
            TimeoutError("provider timed out"),
        ]
    )
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", provider)
    data = json.loads(independent.CORPUS.read_bytes())
    memory = data["memories"][0]
    now = datetime.fromisoformat(data["today"])
    agent, ids = await independent._seed_snapshot(db, [memory], embedder, now)
    question = {"id": "q", "question": memory["content"], "previous": None}
    baseline = await independent._measure(db, question, agent, ids, now, None, None)
    assert baseline["inserted"] == [memory["id"]]
    assert baseline["tokens"] > 0
    with pytest.raises(RuntimeError, match="embedding_unavailable"):
        await independent._measure(db, question, agent, ids, now, embedder, 0.42)
    assert provider.await_count == 2


async def test_evaluator_counts_the_actual_four_selected_user_memories(db: AsyncSession) -> None:
    source = json.loads(independent.CORPUS.read_bytes())["memories"][0]
    memories = [
        {
            **source,
            "id": f"scope-{i}",
            "scope": "user",
            "importance": 1,
            "content": f"갈대 사진 {i}번째 기록",
        }
        for i in range(8)
    ]
    now = datetime(2026, 11, 7)
    agent_id, ids = await independent._seed_snapshot(db, memories, None, now)
    q = {"id": "caps", "question": "갈대 사진", "previous": None}
    selected = await independent._measure(db, q, agent_id, ids, now, None, None)
    assert len(selected["inserted"]) == 4
    assert independent._primary({"gold": [m["id"] for m in memories]}, selected) == 0.5
