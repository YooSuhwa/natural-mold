from __future__ import annotations

from app.agent_runtime.skill_builder.deterministic_eval_runner import (
    run_deterministic_evaluation,
)
from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
    EvalRunCancelled,
)
from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    GraderResultError,
    aggregate_benchmark,
    prepare_eval_output_dirs,
    validate_grader_result,
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
        "with_skill_min_score": 0.8,
        "without_skill_min_score": 0.2,
        "with_skill_max_score": 1,
        "without_skill_max_score": 0.6,
        "with_skill_stddev_score": 0.1,
        "without_skill_stddev_score": 0.2,
    }


def test_aggregate_benchmark_handles_empty_baseline() -> None:
    benchmark = aggregate_benchmark(
        with_skill=[EvalCaseResult(case_index=0, passed=True, score=1)],
        without_skill=[],
    )

    assert benchmark["case_count"] == 1
    assert benchmark["without_skill_pass_rate"] == 0
    assert benchmark["mean_score_delta"] == 1


def test_validate_grader_result_rejects_missing_evidence_claims() -> None:
    weak = {
        "expectations": ["follow instructions"],
        "summary": {"case_count": 1},
        "execution_metrics": {"model_call_count": 1},
        "timing": {"total_seconds": 1},
        "claims": [],
        "eval_feedback": [],
    }

    try:
        validate_grader_result(weak)
    except GraderResultError as exc:
        assert "evidence claim" in str(exc)
    else:
        raise AssertionError("weak grader result should fail")


class RecordingCancellationProbe:
    def __init__(self, *, cancel_at: EvalCancellationPhase | None = None) -> None:
        self.cancel_at = cancel_at
        self.checkpoints: list[EvalCancellationCheckpoint] = []

    async def raise_if_cancelled(self, checkpoint: EvalCancellationCheckpoint) -> None:
        self.checkpoints.append(checkpoint)
        if checkpoint.phase == self.cancel_at:
            raise EvalRunCancelled(checkpoint)


async def test_deterministic_evaluation_checks_cancellation_before_baseline() -> None:
    probe = RecordingCancellationProbe(cancel_at=EvalCancellationPhase.BASELINE_CASE)

    try:
        await run_deterministic_evaluation(
            evals=[{"input": "a"}, {"input": "b"}],
            runner_version="test-runner",
            cancellation=probe,
        )
    except EvalRunCancelled as exc:
        assert exc.checkpoint.phase == EvalCancellationPhase.BASELINE_CASE
        assert exc.checkpoint.case_index == 0
    else:
        raise AssertionError("evaluation should stop at the baseline cancellation checkpoint")

    phases = [checkpoint.phase for checkpoint in probe.checkpoints]
    assert phases == [
        EvalCancellationPhase.START,
        EvalCancellationPhase.WITH_SKILL_CASE,
        EvalCancellationPhase.WITH_SKILL_CASE,
        EvalCancellationPhase.SUBPROCESS_TIMEOUT,
        EvalCancellationPhase.BASELINE_CASE,
    ]


async def test_deterministic_evaluation_checks_cancellation_before_aggregation() -> None:
    probe = RecordingCancellationProbe()

    result = await run_deterministic_evaluation(
        evals=[{"input": "a"}],
        runner_version="test-runner",
        cancellation=probe,
    )

    phases = [checkpoint.phase for checkpoint in probe.checkpoints]
    assert phases == [
        EvalCancellationPhase.START,
        EvalCancellationPhase.WITH_SKILL_CASE,
        EvalCancellationPhase.SUBPROCESS_TIMEOUT,
        EvalCancellationPhase.BASELINE_CASE,
        EvalCancellationPhase.GRADING,
        EvalCancellationPhase.AGGREGATION,
    ]
    assert result.summary["runner_version"] == "test-runner"
    assert result.summary["case_count"] == 1
