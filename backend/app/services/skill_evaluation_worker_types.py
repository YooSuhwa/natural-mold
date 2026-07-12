from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.deterministic_eval_runner import (
    run_deterministic_evaluation,
)
from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationProbe,
    NoopEvalCancellationProbe,
)
from app.agent_runtime.skill_builder.eval_runner import EvalRuntimePolicyError
from app.marketplace.skill_runtime import SkillToolContext
from app.schemas.skill_builder import JsonValue

DEFAULT_RUNNER_VERSION: Final = "deterministic-1"
DEFAULT_GRADER_PROMPT_VERSION: Final = "deterministic-grader-1"


class SkillEvaluationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SkillEvaluationContext:
    run_id: uuid.UUID
    skill_id: uuid.UUID
    evaluation_set_id: uuid.UUID
    skill_version: str | None
    skill_content_hash: str | None
    evals: Sequence[JsonValue]
    runtime_context: SkillToolContext
    cancellation: EvalCancellationProbe = field(default_factory=NoopEvalCancellationProbe)
    # run_config.baseline_comparison — False skips the without-arm (Phase 3 §4).
    baseline_comparison: bool = True


@dataclass(frozen=True, slots=True)
class SkillEvaluationResult:
    summary: dict[str, JsonValue]
    benchmark: dict[str, JsonValue] | None = None
    case_results: list[JsonValue] | None = None
    runner_model: str | None = None
    runner_version: str = DEFAULT_RUNNER_VERSION
    grader_prompt_version: str = DEFAULT_GRADER_PROMPT_VERSION
    eval_schema_version: int = 1
    # Measured LLM usage rollup (Phase 3 §5.1) — None when the evaluator made
    # no model calls (deterministic runner) or predates measurement.
    usage: dict[str, JsonValue] | None = None


class SkillEvaluationEvaluator(Protocol):
    async def evaluate(
        self,
        db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult: ...


@dataclass(slots=True)
class DeterministicSkillEvaluationEvaluator:
    runner_version: str = DEFAULT_RUNNER_VERSION

    async def evaluate(
        self,
        db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        try:
            payload = await run_deterministic_evaluation(
                evals=context.evals,
                runner_version=self.runner_version,
                cancellation=context.cancellation,
                runtime_context=context.runtime_context,
            )
        except EvalRuntimePolicyError as exc:
            raise SkillEvaluationExecutionError(str(exc)) from exc
        return SkillEvaluationResult(
            summary=payload.summary,
            benchmark=payload.benchmark,
            case_results=payload.case_results,
            runner_version=self.runner_version,
        )
