"""The tenant cannot grant a live trial by selecting a model or submitting scores."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import ValidationError
from app.services import memory_recall, memory_recall_policy, memory_vectors, model_service
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, seed_agent
from tests.test_memory_vectors import _embedding, _tenant


def _evaluated_report(model_id: uuid.UUID) -> dict[str, Any]:
    def mode(paraphrase: float, unrelated: float) -> dict:
        categories = {
            category: {
                "metric": "irrelevant_rejection" if category == "unrelated" else "recall_at_10",
                "n": 25,
                "value": {"unrelated": unrelated, "paraphrase": paraphrase}.get(category, 1.0),
                "ci95": [0, 1],
            }
            for category in memory_recall_policy.CATEGORIES
        }
        return {
            "categories": categories,
            "p95_elapsed_ms": {"n": 150, "value": 2, "ci95": [1, 3]},
            "mean_prompt_tokens": {"n": 150, "value": 42, "ci95": [39, 45]},
        }

    return {
        "schema": memory_recall_policy.SCHEMA,
        "corpus_sha256": memory_recall_policy.CORPUS_SHA256,
        "keyword_floor": 0.3,
        "analyzer": "suffix",
        "candidate": {
            "tenant_id": str(TEST_TENANT_ID),
            "model_id": str(model_id),
            "provider": "openai_compatible",
            "base_url": "http://litellm.test/v1",
            "model": "text-embedding-3-large",
            "dimensions": 1024,
            "cosine_floor": 0.42,
        },
        "modes": {"keyword": mode(0.3, 1.0), "hybrid": mode(0.6, 0.96)},
        "gate": {"passed": True},
    }


def test_report_corpus_hash_matches_reviewed_file() -> None:
    corpus = Path(__file__).resolve().parents[1] / "evals/memory_recall/independent_ko.json"
    assert hashlib.sha256(corpus.read_bytes()).hexdigest() == memory_recall_policy.CORPUS_SHA256


@pytest.mark.asyncio
async def test_qualification_and_admin_selection_are_both_required(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_agent(db)
    model = await model_service.create_model(db, data=_embedding())
    other = await model_service.create_model(db, data=_embedding(model_name="another"))
    tenant = await _tenant(db)
    context = unit_context(TEST_USER_ID)
    await db.commit()
    monkeypatch.setattr(settings, "memory_recall_quality_dir", tmp_path)

    async def choose(
        mode: memory_recall_policy.RecallMode | None = None,
    ) -> memory_vectors.VectorStatus:
        return await memory_vectors.change(
            db, tenant, context, model_id=model.id, confirm_count=0, recall_mode=mode
        )

    assert (await choose()).recall_mode == "off"
    with pytest.raises(ValidationError, match="독립 평가"):
        await choose("shadow")
    with pytest.raises(ValidationError, match="현재 설정"):
        await memory_vectors.change(
            db, tenant, context, model_id=other.id, confirm_count=0, recall_mode="serve"
        )

    path = tmp_path / str(tenant.id) / f"{model.id}.json"
    path.parent.mkdir()
    report = _evaluated_report(model.id)
    path.write_text(json.dumps(report))
    measured = await memory_vectors.status(db, tenant, org_id=context.org_id)
    assert measured.quality_floor == 0.42 and measured.recall_mode == "off"
    assert (await choose("shadow")).recall_mode == "shadow"
    assert (
        memory_recall.vector_floor(
            await memory_vectors.embedder_for(db, tenant, org_id=context.org_id)
        )
        is None
    )
    assert (await choose("serve")).recall_mode == "serve"
    assert (
        memory_recall.vector_floor(
            await memory_vectors.embedder_for(db, tenant, org_id=context.org_id)
        )
        == 0.42
    )

    # The administrator consented to one measured floor. Replacing the file does not
    # extend that consent to a different threshold; removing it disables serving at once.
    report["candidate"]["cosine_floor"] = 0.435
    path.write_text(json.dumps(report))
    assert (await memory_vectors.status(db, tenant, org_id=context.org_id)).recall_mode == "off"
    assert (await choose("serve")).quality_floor == 0.435
    monkeypatch.setattr(settings, "opensearch_url", "http://nori.test")
    assert (await memory_vectors.status(db, tenant, org_id=context.org_id)).recall_mode == "off"
    monkeypatch.setattr(settings, "opensearch_url", "")
    path.unlink()
    assert (
        memory_recall.vector_floor(
            await memory_vectors.embedder_for(db, tenant, org_id=context.org_id)
        )
        is None
    )

    model.is_visible = False
    await db.commit()
    assert (await choose("off")).recall_mode == "off"
    model.is_visible = True
    await db.commit()
    path.write_text(json.dumps(report))
    restored = await memory_vectors.status(db, tenant, org_id=context.org_id)
    assert restored.quality_floor == 0.435 and restored.recall_mode == "off"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("gate", "passed"), False),
        (("corpus_sha256",), "old"),
        (("analyzer",), "nori"),
        (("keyword_floor",), 0.4),
        (("candidate", "tenant_id"), "other"),
        (("candidate", "model_id"), "other"),
        (("candidate", "base_url"), "https://different.test/v1"),
        (("candidate", "provider"), "openai"),
        (("candidate", "dimensions"), 512),
        (("modes", "hybrid", "categories", "unrelated", "value"), 0.89),
        (("modes", "hybrid", "categories", "paraphrase", "value"), 0.3),
        (("modes", "hybrid", "categories", "particle", "n"), 24),
        (("modes", "hybrid", "categories", "time", "ci95"), None),
        (("modes", "hybrid", "p95_elapsed_ms", "n"), 42),
    ],
)
def test_unqualified_evidence_never_enables_a_trial(
    path: tuple[str, ...], value: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_id = uuid.uuid4()
    report = _evaluated_report(model_id)
    node = report
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    output = tmp_path / str(TEST_TENANT_ID) / f"{model_id}.json"
    output.parent.mkdir()
    output.write_text(json.dumps(report))
    monkeypatch.setattr(settings, "memory_recall_quality_dir", tmp_path)
    assert (
        memory_recall_policy.passing_floor(
            tenant_id=TEST_TENANT_ID,
            model_id=model_id,
            provider="openai_compatible",
            base_url="http://litellm.test/v1",
            model_name="text-embedding-3-large",
            dimensions=1024,
        )
        is None
    )
