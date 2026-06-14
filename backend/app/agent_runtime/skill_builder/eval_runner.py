from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.schemas.skill_builder import JsonValue


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
    with_mean = _mean_score(with_skill)
    without_mean = _mean_score(without_skill)
    return {
        "case_count": max(len(with_skill), len(without_skill)),
        "with_skill_pass_rate": with_pass_rate,
        "without_skill_pass_rate": without_pass_rate,
        "pass_rate_delta": round(with_pass_rate - without_pass_rate, 6),
        "with_skill_mean_score": with_mean,
        "without_skill_mean_score": without_mean,
        "mean_score_delta": round(with_mean - without_mean, 6),
    }


def _pass_rate(results: list[EvalCaseResult]) -> float:
    if not results:
        return 0
    passed = sum(1 for result in results if result.passed)
    return round(passed / len(results), 6)


def _mean_score(results: list[EvalCaseResult]) -> float:
    if not results:
        return 0
    return round(sum(result.score for result in results) / len(results), 6)
