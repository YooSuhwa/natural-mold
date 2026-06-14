from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Final

from app.schemas.skill_builder import JsonValue

REQUIRED_GRADER_RESULT_KEYS: Final = (
    "expectations",
    "summary",
    "execution_metrics",
    "timing",
    "claims",
    "eval_feedback",
)


class GraderResultError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvalOutputDirs:
    root: Path
    with_skill: Path
    without_skill: Path


@dataclass(frozen=True, slots=True)
class EvalCaseResult:
    case_index: int
    passed: bool
    score: float


def prepare_eval_output_dirs(root: Path, *, run_id: str) -> EvalOutputDirs:
    run_root = root / run_id
    with_skill = run_root / "with-skill"
    without_skill = run_root / "without-skill"
    with_skill.mkdir(parents=True, exist_ok=True)
    without_skill.mkdir(parents=True, exist_ok=True)
    return EvalOutputDirs(root=run_root, with_skill=with_skill, without_skill=without_skill)


def aggregate_benchmark(
    *,
    with_skill: list[EvalCaseResult],
    without_skill: list[EvalCaseResult],
) -> dict[str, JsonValue]:
    with_pass_rate = _pass_rate(with_skill)
    without_pass_rate = _pass_rate(without_skill)
    with_stats = _score_stats(with_skill)
    without_stats = _score_stats(without_skill)
    return {
        "case_count": max(len(with_skill), len(without_skill)),
        "with_skill_pass_rate": with_pass_rate,
        "without_skill_pass_rate": without_pass_rate,
        "pass_rate_delta": round(with_pass_rate - without_pass_rate, 6),
        "with_skill_mean_score": with_stats["mean"],
        "without_skill_mean_score": without_stats["mean"],
        "mean_score_delta": round(with_stats["mean"] - without_stats["mean"], 6),
        "with_skill_min_score": with_stats["min"],
        "without_skill_min_score": without_stats["min"],
        "with_skill_max_score": with_stats["max"],
        "without_skill_max_score": without_stats["max"],
        "with_skill_stddev_score": with_stats["stddev"],
        "without_skill_stddev_score": without_stats["stddev"],
    }


def validate_grader_result(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    missing = [key for key in REQUIRED_GRADER_RESULT_KEYS if key not in result]
    if missing:
        raise GraderResultError(f"grader result missing keys: {', '.join(missing)}")
    claims = result["claims"]
    if not isinstance(claims, list) or not claims:
        raise GraderResultError("grader result must include at least one evidence claim")
    feedback = result["eval_feedback"]
    if not isinstance(feedback, list):
        raise GraderResultError("grader result eval_feedback must be a list")
    return result


def _pass_rate(results: list[EvalCaseResult]) -> float:
    if not results:
        return 0
    passed = sum(1 for result in results if result.passed)
    return round(passed / len(results), 6)


def _score_stats(results: list[EvalCaseResult]) -> dict[str, float]:
    if not results:
        return {"mean": 0, "min": 0, "max": 0, "stddev": 0}
    scores = [result.score for result in results]
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    return {
        "mean": round(mean, 6),
        "min": round(min(scores), 6),
        "max": round(max(scores), 6),
        "stddev": round(sqrt(variance), 6),
    }
