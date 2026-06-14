from __future__ import annotations

from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    aggregate_benchmark,
    prepare_eval_output_dirs,
)


def test_prepare_eval_output_dirs_creates_with_skill_and_baseline_dirs(tmp_path) -> None:
    dirs = prepare_eval_output_dirs(tmp_path, run_id="run-1")

    assert dirs.root == tmp_path / "run-1"
    assert dirs.with_skill == tmp_path / "run-1" / "with-skill"
    assert dirs.without_skill == tmp_path / "run-1" / "without-skill"
    assert dirs.with_skill.is_dir()
    assert dirs.without_skill.is_dir()


def test_aggregate_benchmark_computes_pass_rates_and_delta() -> None:
    benchmark = aggregate_benchmark(
        with_skill=[
            EvalCaseResult(case_index=0, passed=True, score=1),
            EvalCaseResult(case_index=1, passed=True, score=0.8),
        ],
        without_skill=[
            EvalCaseResult(case_index=0, passed=False, score=0.2),
            EvalCaseResult(case_index=1, passed=True, score=0.6),
        ],
    )

    assert benchmark == {
        "case_count": 2,
        "with_skill_pass_rate": 1.0,
        "without_skill_pass_rate": 0.5,
        "pass_rate_delta": 0.5,
        "with_skill_mean_score": 0.9,
        "without_skill_mean_score": 0.4,
        "mean_score_delta": 0.5,
    }


def test_aggregate_benchmark_handles_empty_baseline() -> None:
    benchmark = aggregate_benchmark(
        with_skill=[EvalCaseResult(case_index=0, passed=True, score=1)],
        without_skill=[],
    )

    assert benchmark["case_count"] == 1
    assert benchmark["without_skill_pass_rate"] == 0
    assert benchmark["mean_score_delta"] == 1
