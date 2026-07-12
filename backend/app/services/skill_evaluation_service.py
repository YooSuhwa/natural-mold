from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.agent_runtime.skill_builder.eval_limits import MAX_SKILL_EVAL_CASES, MIN_SKILL_EVAL_CASES
from app.config import settings
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_evaluation import SkillEvaluationRunEstimate
from app.services.skill_evaluation_case_limits import (
    SkillEvaluationCaseSizeError,
    validate_evaluation_case_sizes,
)

CANCELLABLE_STATUSES = frozenset({"queued", "running", "grading"})


class SkillEvaluationRunNotCancellable(RuntimeError):
    pass


class SkillEvaluationSetTooLarge(ValueError):
    pass


class SkillEvaluationSetEmpty(ValueError):
    pass


async def create_evaluation_set(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    name: str,
    evals: list[Any],
    description: str | None = None,
    source_kind: str = "builder",
    template_key: str | None = None,
    template_version: str | None = None,
    generation_strategy: dict[str, Any] | None = None,
) -> SkillEvaluationSet:
    if len(evals) < MIN_SKILL_EVAL_CASES:
        raise SkillEvaluationSetEmpty("evaluation sets require at least one case")
    if len(evals) > MAX_SKILL_EVAL_CASES:
        raise SkillEvaluationSetTooLarge(
            f"evaluation sets can contain at most {MAX_SKILL_EVAL_CASES} cases"
        )
    try:
        validate_evaluation_case_sizes(evals)
    except SkillEvaluationCaseSizeError as exc:
        raise SkillEvaluationSetTooLarge(str(exc)) from exc
    evaluation_set = SkillEvaluationSet(
        user_id=user_id,
        skill_id=skill.id,
        name=name,
        description=description,
        source_kind=source_kind,
        template_key=template_key,
        template_version=template_version,
        generation_strategy=generation_strategy,
        evals=evals,
    )
    db.add(evaluation_set)
    await db.flush()
    return evaluation_set


async def list_evaluation_sets(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> list[SkillEvaluationSet]:
    result = await db.execute(
        select(SkillEvaluationSet)
        .where(SkillEvaluationSet.skill_id == skill.id, SkillEvaluationSet.user_id == user_id)
        .order_by(desc(SkillEvaluationSet.updated_at), desc(SkillEvaluationSet.created_at))
    )
    return list(result.scalars().all())


async def get_evaluation_set(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
) -> SkillEvaluationSet | None:
    result = await db.execute(
        select(SkillEvaluationSet).where(
            SkillEvaluationSet.id == evaluation_set_id,
            SkillEvaluationSet.skill_id == skill.id,
            SkillEvaluationSet.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_runs(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set: SkillEvaluationSet,
) -> list[SkillEvaluationRun]:
    result = await db.execute(
        select(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.skill_id == skill.id,
            SkillEvaluationRun.user_id == user_id,
            SkillEvaluationRun.evaluation_set_id == evaluation_set.id,
        )
        .order_by(desc(SkillEvaluationRun.created_at))
    )
    return list(result.scalars().all())


async def latest_runs_by_evaluation_set(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set_ids: list[uuid.UUID],
) -> dict[uuid.UUID, SkillEvaluationRun]:
    if not evaluation_set_ids:
        return {}
    result = await db.execute(
        select(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.skill_id == skill.id,
            SkillEvaluationRun.user_id == user_id,
            SkillEvaluationRun.evaluation_set_id.in_(evaluation_set_ids),
        )
        .order_by(SkillEvaluationRun.evaluation_set_id, desc(SkillEvaluationRun.created_at))
    )
    latest: dict[uuid.UUID, SkillEvaluationRun] = {}
    for run in result.scalars():
        latest.setdefault(run.evaluation_set_id, run)
    return latest


async def get_run(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set: SkillEvaluationSet,
    run_id: uuid.UUID,
) -> SkillEvaluationRun | None:
    result = await db.execute(
        select(SkillEvaluationRun).where(
            SkillEvaluationRun.id == run_id,
            SkillEvaluationRun.skill_id == skill.id,
            SkillEvaluationRun.user_id == user_id,
            SkillEvaluationRun.evaluation_set_id == evaluation_set.id,
        )
    )
    return result.scalar_one_or_none()


# Token heuristics for the pre-run cost estimate (spec §5.2). Deliberately
# coarse — chars/4 plus flat per-call constants for prompt framing (grader
# system prompt, skill payload) and completions (arm answers, grader JSON).
_ESTIMATE_CHARS_PER_TOKEN = 4
_ESTIMATE_PROMPT_OVERHEAD_TOKENS_PER_CALL = 800
_ESTIMATE_COMPLETION_TOKENS_PER_CALL = 400


def _estimated_case_tokens(evaluation_set: SkillEvaluationSet) -> int:
    chars = 0
    for case in evaluation_set.evals or []:
        if not isinstance(case, dict):
            continue
        for field in ("input", "expected"):
            value = case.get(field)
            if isinstance(value, str):
                chars += len(value)
    return chars // _ESTIMATE_CHARS_PER_TOKEN


def estimate_run(
    evaluation_set: SkillEvaluationSet,
    *,
    uses_baseline_comparison: bool = True,
) -> SkillEvaluationRunEstimate:
    case_count = len(evaluation_set.evals or [])
    model_calls_per_case = 3 if uses_baseline_comparison else 2
    model_call_count = case_count * model_calls_per_case
    estimated_seconds = min(
        settings.skill_evaluation_run_timeout_seconds,
        case_count * settings.skill_evaluation_case_timeout_seconds,
    )
    case_tokens = _estimated_case_tokens(evaluation_set)
    tokens_in = (
        case_tokens * model_calls_per_case
        + model_call_count * _ESTIMATE_PROMPT_OVERHEAD_TOKENS_PER_CALL
    )
    tokens_out = model_call_count * _ESTIMATE_COMPLETION_TOKENS_PER_CALL
    return SkillEvaluationRunEstimate(
        case_count=case_count,
        model_call_count=model_call_count,
        estimated_seconds=estimated_seconds,
        timeout_seconds=settings.skill_evaluation_run_timeout_seconds,
        estimated_tokens_in=tokens_in,
        estimated_tokens_out=tokens_out,
        estimated_cost_usd=0,
        pricing_available=False,
        uses_baseline_comparison=uses_baseline_comparison,
    )


async def estimate_run_priced(
    db: AsyncSession,
    evaluation_set: SkillEvaluationSet,
    *,
    uses_baseline_comparison: bool = True,
) -> SkillEvaluationRunEstimate:
    """Estimate with real per-token pricing of the current runner model.

    The runner model is the ``text_primary`` System LLM (same resolution the
    evaluator uses); when the slot is unset or the model has no pricing the
    estimate degrades to ``estimated_cost_usd=0`` + ``pricing_available=False``
    so the UI can say "단가 미설정" instead of implying "free" (spec §5.2).
    """

    estimate = estimate_run(evaluation_set, uses_baseline_comparison=uses_baseline_comparison)
    from app.models.system_llm_setting import SystemLlmSetting
    from app.services.skill_evaluation_usage import resolve_model_pricing

    runner_model = (
        await db.execute(
            select(SystemLlmSetting.model_name)
            .where(SystemLlmSetting.role == "text_primary")
            .limit(1)
        )
    ).scalar_one_or_none()
    if not runner_model:
        return estimate
    pricing = await resolve_model_pricing(db, runner_model)
    cost = pricing.cost_for(estimate.estimated_tokens_in, estimate.estimated_tokens_out)
    return estimate.model_copy(
        update={
            "runner_model": runner_model,
            "pricing_available": pricing.available,
            "estimated_cost_usd": float(cost) if cost is not None else 0,
        }
    )


def _run_config_baseline(run_config: dict[str, Any] | None) -> bool:
    if isinstance(run_config, dict) and isinstance(run_config.get("baseline_comparison"), bool):
        return run_config["baseline_comparison"]
    return True


async def create_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    evaluation_set: SkillEvaluationSet,
    run_config: dict[str, Any] | None = None,
) -> SkillEvaluationRun:
    # The persisted estimate must match the run the worker will actually
    # execute — a baseline-off run makes 2 calls/case, not 3 (spec §4.1).
    estimate = await estimate_run_priced(
        db,
        evaluation_set,
        uses_baseline_comparison=_run_config_baseline(run_config),
    )
    run = SkillEvaluationRun(
        user_id=user_id,
        skill_id=skill.id,
        evaluation_set_id=evaluation_set.id,
        status="queued",
        skill_version=skill.version,
        skill_content_hash=skill.content_hash,
        run_config=run_config,
        estimate=estimate.model_dump(mode="json"),
    )
    db.add(run)
    await db.flush()
    return run


@dataclass(frozen=True, slots=True)
class SkillVersionStats:
    skill_version: str | None
    content_hash: str | None
    run_count: int
    latest_pass_rate: float | None
    avg_pass_rate: float | None
    latest_pass_rate_delta: float | None
    latest_measured: bool
    first_run_at: datetime
    last_run_at: datetime


async def version_stats(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> list[SkillVersionStats]:
    """Completed runs grouped by (skill_version, content_hash) — Phase 3 §6.

    ``pass_rate`` lives inside the summary JSON, so grouping happens in
    Python; per-skill run counts are small (worker queue is bounded). Only the
    lightweight columns are loaded — the heavy ``case_results``/``estimate``/
    ``usage`` JSON blobs are never read here, so ``load_only`` keeps this scan
    cheap even for skills with many runs. Ordered chronologically by last run.
    """

    rows = (
        (
            await db.execute(
                select(SkillEvaluationRun)
                .where(
                    SkillEvaluationRun.skill_id == skill.id,
                    SkillEvaluationRun.user_id == user_id,
                    SkillEvaluationRun.status == "completed",
                )
                .options(
                    load_only(
                        SkillEvaluationRun.skill_version,
                        SkillEvaluationRun.skill_content_hash,
                        SkillEvaluationRun.summary,
                        SkillEvaluationRun.benchmark,
                        SkillEvaluationRun.created_at,
                    )
                )
                .order_by(SkillEvaluationRun.created_at.asc(), SkillEvaluationRun.id.asc())
            )
        )
        .scalars()
        .all()
    )
    groups: dict[tuple[str | None, str | None], list[SkillEvaluationRun]] = {}
    for run in rows:
        groups.setdefault((run.skill_version, run.skill_content_hash), []).append(run)

    stats = [
        _version_group_stats(version, content_hash, runs)
        for (version, content_hash), runs in groups.items()
    ]
    stats.sort(key=lambda item: item.last_run_at)
    return stats


def _version_group_stats(
    version: str | None,
    content_hash: str | None,
    runs: list[SkillEvaluationRun],
) -> SkillVersionStats:
    pass_rates = [rate for rate in (_run_pass_rate(run) for run in runs) if rate is not None]
    latest = runs[-1]
    latest_benchmark = latest.benchmark if isinstance(latest.benchmark, dict) else {}
    delta = latest_benchmark.get("pass_rate_delta")
    return SkillVersionStats(
        skill_version=version,
        content_hash=content_hash,
        run_count=len(runs),
        latest_pass_rate=_run_pass_rate(latest),
        avg_pass_rate=(round(sum(pass_rates) / len(pass_rates), 6) if pass_rates else None),
        latest_pass_rate_delta=(
            float(delta) if isinstance(delta, int | float) and not isinstance(delta, bool) else None
        ),
        latest_measured=latest_benchmark.get("measured") is True,
        first_run_at=runs[0].created_at,
        last_run_at=latest.created_at,
    )


def _run_pass_rate(run: SkillEvaluationRun) -> float | None:
    summary = run.summary if isinstance(run.summary, dict) else {}
    value = summary.get("pass_rate")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


async def cancel_run(
    db: AsyncSession,
    run: SkillEvaluationRun,
    *,
    reason: str,
) -> SkillEvaluationRun:
    if run.status not in CANCELLABLE_STATUSES:
        raise SkillEvaluationRunNotCancellable(f"run status is not cancellable: {run.status}")
    now = _now()
    result = await db.execute(
        update(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.id == run.id,
            SkillEvaluationRun.status.in_(CANCELLABLE_STATUSES),
        )
        .values(
            status="cancelled",
            cancellation_requested_at=now,
            cancellation_reason=reason[:120],
            completed_at=now,
        )
    )
    await db.flush()
    if result.rowcount != 1:
        await db.refresh(run)
        raise SkillEvaluationRunNotCancellable(f"run status is not cancellable: {run.status}")
    await db.refresh(run)
    return run


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
