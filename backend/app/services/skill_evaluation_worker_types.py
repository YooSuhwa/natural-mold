from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.agent_runtime.skill_builder.eval_runner import EvalCaseResult, aggregate_benchmark
from app.schemas.skill_builder import JsonValue


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


@dataclass(frozen=True, slots=True)
class SkillEvaluationResult:
    summary: dict[str, JsonValue]
    benchmark: dict[str, JsonValue] | None = None
    case_results: list[JsonValue] | None = None


class SkillEvaluationEvaluator(Protocol):
    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult: ...


@dataclass(slots=True)
class DeterministicSkillEvaluationEvaluator:
    runner_version: str = "deterministic-1"

    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        with_skill_results = [
            EvalCaseResult(case_index=index, passed=True, score=1)
            for index, _case in enumerate(context.evals)
        ]
        without_skill_results = [
            EvalCaseResult(case_index=index, passed=False, score=0)
            for index, _case in enumerate(context.evals)
        ]
        case_results: list[JsonValue] = [
            {
                "case_index": index,
                "status": "passed",
                "input": case,
                "score": 1,
                "notes": "Deterministic placeholder result.",
            }
            for index, case in enumerate(context.evals)
        ]
        case_count = len(case_results)
        pass_rate = 1 if case_count else 0
        return SkillEvaluationResult(
            summary={
                "runner_version": self.runner_version,
                "case_count": case_count,
                "passed_count": case_count,
                "failed_count": 0,
                "pass_rate": pass_rate,
            },
            benchmark=aggregate_benchmark(
                with_skill=with_skill_results,
                without_skill=without_skill_results,
            ),
            case_results=case_results,
        )
