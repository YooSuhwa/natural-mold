from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    aggregate_benchmark,
    validate_grader_result,
)
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


@dataclass(frozen=True, slots=True)
class SkillEvaluationResult:
    summary: dict[str, JsonValue]
    benchmark: dict[str, JsonValue] | None = None
    case_results: list[JsonValue] | None = None
    runner_model: str | None = None
    runner_version: str = DEFAULT_RUNNER_VERSION
    grader_prompt_version: str = DEFAULT_GRADER_PROMPT_VERSION
    eval_schema_version: int = 1


class SkillEvaluationEvaluator(Protocol):
    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult: ...


@dataclass(slots=True)
class DeterministicSkillEvaluationEvaluator:
    runner_version: str = DEFAULT_RUNNER_VERSION

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
        grader_result = validate_grader_result(
            {
                "expectations": [
                    case.get("expected") for case in context.evals if isinstance(case, dict)
                ],
                "summary": {
                    "runner_version": self.runner_version,
                    "case_count": case_count,
                    "passed_count": case_count,
                    "failed_count": 0,
                    "pass_rate": pass_rate,
                },
                "execution_metrics": {
                    "with_skill_runs": case_count,
                    "without_skill_runs": case_count,
                    "model_call_count": case_count * 3,
                },
                "timing": {
                    "case_timeout_seconds": 0,
                    "total_seconds": 0,
                },
                "claims": [
                    {
                        "case_index": index,
                        "supported": True,
                        "evidence": "Deterministic evaluator accepted the case.",
                    }
                    for index, _case in enumerate(context.evals)
                ],
                "eval_feedback": [
                    {
                        "case_index": index,
                        "severity": "info",
                        "message": "Deterministic placeholder result.",
                    }
                    for index, _case in enumerate(context.evals)
                ],
            }
        )
        summary = dict(grader_result)
        summary["runner_version"] = self.runner_version
        summary["case_count"] = case_count
        summary["passed_count"] = case_count
        summary["failed_count"] = 0
        summary["pass_rate"] = pass_rate
        return SkillEvaluationResult(
            summary=summary,
            benchmark=aggregate_benchmark(
                with_skill=with_skill_results,
                without_skill=without_skill_results,
            ),
            case_results=case_results,
            runner_version=self.runner_version,
        )
