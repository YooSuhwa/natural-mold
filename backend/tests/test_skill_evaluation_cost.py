"""Phase 3 비용 실회계 — estimate 실단가 계산 + usage 캡처 유틸."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.skill_evaluation import SkillEvaluationSet
from app.models.system_llm_setting import SystemLlmSetting
from app.services.skill_evaluation_service import estimate_run, estimate_run_priced
from app.services.skill_evaluation_usage import (
    UNPRICED,
    LlmUsageCollector,
    ModelPricing,
    resolve_model_pricing,
)
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


def _evaluation_set(evals: list) -> SkillEvaluationSet:
    return SkillEvaluationSet(
        user_id=TEST_USER_ID,
        name="estimate",
        evals=evals,
    )


# ---------------------------------------------------------------------------
# estimate_run — token heuristic
# ---------------------------------------------------------------------------


async def test_estimate_run_computes_token_heuristic() -> None:
    # 400 chars of case text → 100 case tokens.
    evals = [{"input": "a" * 300, "expected": "b" * 100}]
    estimate = estimate_run(_evaluation_set(evals))

    assert estimate.case_count == 1
    assert estimate.model_call_count == 3
    # case tokens ride every call + flat per-call overhead.
    assert estimate.estimated_tokens_in == 100 * 3 + 3 * 800
    assert estimate.estimated_tokens_out == 3 * 400
    assert estimate.estimated_cost_usd == 0
    assert estimate.pricing_available is False


async def test_estimate_run_without_baseline_uses_two_calls() -> None:
    estimate = estimate_run(_evaluation_set([{"input": "x"}]), uses_baseline_comparison=False)
    assert estimate.model_call_count == 2
    assert estimate.uses_baseline_comparison is False


async def test_create_run_threads_baseline_into_persisted_estimate(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    """review R1 — a baseline-off run must persist a 2-call estimate, not 3."""

    from unittest.mock import patch

    from app.skills import service as skill_service

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Baseline",
            slug="baseline-estimate",
            description="Use when testing baseline estimate threading.",
            content='---\nname: baseline\ndescription: "Use when testing."\n---\n\nBody.\n',
            version="1.0.0",
        )
    eval_set = SkillEvaluationSet(
        user_id=TEST_USER_ID,
        skill_id=skill.id,
        name="baseline",
        evals=[{"input": "x"}, {"input": "y"}],
    )
    db.add(eval_set)
    await db.flush()

    from app.services.skill_evaluation_service import create_run

    run = await create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=eval_set,
        run_config={"baseline_comparison": False},
    )
    assert run.estimate is not None
    assert run.estimate["uses_baseline_comparison"] is False
    assert run.estimate["model_call_count"] == 4  # 2 cases × 2 calls, not ×3


# ---------------------------------------------------------------------------
# estimate_run_priced — real pricing via system LLM model
# ---------------------------------------------------------------------------


async def _seed_priced_system_model(db: AsyncSession, model_name: str = "priced-model") -> None:
    db.add(
        Model(
            provider="openai",
            model_name=model_name,
            display_name="Priced",
            cost_per_input_token=Decimal("0.00000100"),
            cost_per_output_token=Decimal("0.00000200"),
        )
    )
    db.add(SystemLlmSetting(role="text_primary", model_name=model_name))
    await db.commit()


async def test_estimate_run_priced_fills_cost(db: AsyncSession) -> None:
    await _seed_priced_system_model(db)
    evals = [{"input": "a" * 400}]  # 100 case tokens

    estimate = await estimate_run_priced(db, _evaluation_set(evals))

    assert estimate.runner_model == "priced-model"
    assert estimate.pricing_available is True
    expected_cost = estimate.estimated_tokens_in * 0.000001 + estimate.estimated_tokens_out * 2e-6
    assert estimate.estimated_cost_usd == pytest.approx(expected_cost, rel=1e-4)


async def test_estimate_run_priced_degrades_without_system_model(db: AsyncSession) -> None:
    estimate = await estimate_run_priced(db, _evaluation_set([{"input": "x"}]))
    assert estimate.runner_model is None
    assert estimate.pricing_available is False
    assert estimate.estimated_cost_usd == 0


async def test_estimate_run_priced_degrades_without_pricing(db: AsyncSession) -> None:
    db.add(Model(provider="openai", model_name="unpriced-model", display_name="Unpriced"))
    db.add(SystemLlmSetting(role="text_primary", model_name="unpriced-model"))
    await db.commit()

    estimate = await estimate_run_priced(db, _evaluation_set([{"input": "x"}]))
    assert estimate.runner_model == "unpriced-model"
    assert estimate.pricing_available is False
    assert estimate.estimated_cost_usd == 0


# ---------------------------------------------------------------------------
# resolve_model_pricing / LlmUsageCollector
# ---------------------------------------------------------------------------


async def test_resolve_model_pricing_prefers_priced_row(db: AsyncSession) -> None:
    db.add(Model(provider="manual", model_name="dup-model", display_name="Unpriced dup"))
    db.add(
        Model(
            provider="openai",
            model_name="dup-model",
            display_name="Priced dup",
            cost_per_input_token=Decimal("0.000001"),
            cost_per_output_token=Decimal("0.000002"),
        )
    )
    await db.commit()

    pricing = await resolve_model_pricing(db, "dup-model")
    assert pricing.available is True


async def test_resolve_model_pricing_unknown_model(db: AsyncSession) -> None:
    pricing = await resolve_model_pricing(db, "nope")
    assert pricing is UNPRICED
    assert pricing.cost_for(100, 100) is None


async def test_usage_collector_rollup_with_pricing() -> None:
    collector = LlmUsageCollector()
    collector.add_response(
        SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 40})
    )
    collector.add_response(SimpleNamespace(usage_metadata=None))  # scripted model — no usage

    pricing = ModelPricing(
        cost_per_input_token=Decimal("0.000001"),
        cost_per_output_token=Decimal("0.000002"),
    )
    rollup = collector.rollup(pricing)

    assert rollup["measured"] is True
    assert rollup["model_calls"] == 2
    assert rollup["tokens_in"] == 100
    assert rollup["tokens_out"] == 40
    assert rollup["cost_usd"] == pytest.approx(0.00018)


async def test_usage_collector_rollup_without_pricing() -> None:
    collector = LlmUsageCollector()
    collector.add_response(SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}))
    rollup = collector.rollup(UNPRICED)
    assert rollup["cost_usd"] is None


async def test_usage_collector_priced_but_no_metadata_is_unknown_not_free() -> None:
    """review R3 — a priced model that returns NO usage_metadata must report
    cost None (unknown), never a $0 that masquerades as a free measured run.
    """

    collector = LlmUsageCollector()
    collector.add_response(SimpleNamespace(usage_metadata=None))
    collector.add_response(SimpleNamespace(usage_metadata=None))

    priced = ModelPricing(
        cost_per_input_token=Decimal("0.000001"),
        cost_per_output_token=Decimal("0.000002"),
    )
    rollup = collector.rollup(priced)

    assert rollup["model_calls"] == 2
    assert rollup["tokens_in"] == 0
    assert rollup["cost_usd"] is None  # unknown, NOT 0.0


async def test_estimate_run_counts_structured_input_tokens() -> None:
    """review R3 — structured (dict) inputs are json-serialized into the prompt,
    so the token estimate must count them (a str-only heuristic reports ~0).
    """

    structured = estimate_run(
        _evaluation_set([{"input": {"query": "x" * 400, "context": ["y" * 400]}}])
    )
    empty = estimate_run(_evaluation_set([{"input": None}]))
    # Structured case contributes real serialized chars beyond the flat overhead.
    assert structured.estimated_tokens_in > empty.estimated_tokens_in
