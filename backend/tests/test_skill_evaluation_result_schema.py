from __future__ import annotations

from app.services.skill_evaluation_result_schema import normalize_skill_evaluation_result


def test_summary_v2_keeps_legacy_fields_and_adds_kpis() -> None:
    # Given: one passed case and legacy grader summary metadata.
    evals = [{"input": "Extract action items.", "expected": "Action item table."}]
    raw_case_results = [
        {
            "case_index": 0,
            "status": "passed",
            "score": 0.92,
            "baseline_status": "failed",
            "baseline_score": 0.2,
            "grader_feedback": "Matches the expectation.",
            "evidence": "Owner and due date were extracted.",
        }
    ]
    raw_summary = {
        "runner_version": "llm-1",
        "claims": [{"case_index": 0, "supported": True}],
        "eval_feedback": [{"case_index": 0, "severity": "info"}],
        "execution_metrics": {"model_call_count": 3},
        "timing": {"timeout_seconds": 30},
    }
    raw_benchmark = {
        "with_skill_pass_rate": 1.0,
        "without_skill_pass_rate": 0.0,
        "pass_rate_delta": 1.0,
    }

    # When: the result is normalized to the v2 contract.
    summary, _benchmark, _case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
        raw_summary=raw_summary,
        raw_benchmark=raw_benchmark,
    )

    # Then: legacy fields remain and KPI fields are available.
    assert summary["schema_version"] == 2
    assert summary["runner_version"] == "llm-1"
    assert summary["case_count"] == 1
    assert summary["passed_count"] == 1
    assert summary["failed_count"] == 0
    assert summary["pass_rate"] == 1.0
    assert summary["claims"] == [{"case_index": 0, "supported": True}]
    assert summary["eval_feedback"] == [{"case_index": 0, "severity": "info"}]
    kpis = summary["kpis"]
    assert isinstance(kpis, dict)
    assert kpis["pass_rate"] == {
        "value": 1.0,
        "passed": 1,
        "total": 1,
        "delta": 1.0,
        "direction": "higher_is_better",
    }
    assert kpis["error_count"] == {
        "value": 0,
        "baseline_value": 1,
        "delta": -1,
        "direction": "lower_is_better",
    }


def test_benchmark_v2_keeps_legacy_fields_and_adds_comparison() -> None:
    # Given: legacy benchmark fields and per-case duration/token metrics.
    evals = [{"input": "Summarize.", "expected": "Short summary."}]
    raw_case_results = [
        {
            "case_index": 0,
            "status": "passed",
            "score": 1.0,
            "baseline_status": "failed",
            "baseline_score": 0.1,
            "duration_ms": 1200,
            "baseline_duration_ms": 2200,
            "tokens": 300,
            "baseline_tokens": 500,
        }
    ]
    raw_benchmark = {
        "case_count": 1,
        "with_skill_pass_rate": 1.0,
        "without_skill_pass_rate": 0.0,
        "pass_rate_delta": 1.0,
        "with_skill_mean_score": 1.0,
    }

    # When: the benchmark is normalized to the v2 contract.
    _summary, benchmark, _case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
        raw_benchmark=raw_benchmark,
    )

    # Then: existing fields remain and nested comparison is computed.
    assert benchmark["case_count"] == 1
    assert benchmark["with_skill_mean_score"] == 1.0
    assert benchmark["with_skill_pass_rate"] == 1.0
    assert benchmark["without_skill_pass_rate"] == 0.0
    assert benchmark["pass_rate_delta"] == 1.0
    assert benchmark["duration_delta_ms"] == -1000
    assert benchmark["token_delta"] == -200
    assert benchmark["quality_delta"] == 1.0
    assert benchmark["comparison"] == {
        "pass_rate": {
            "with_skill": 1.0,
            "baseline": 0.0,
            "delta": 1.0,
            "direction": "higher_is_better",
        },
        "duration_ms": {
            "with_skill": 1200,
            "baseline": 2200,
            "delta": -1000,
            "direction": "lower_is_better",
        },
        "tokens": {
            "with_skill": 300,
            "baseline": 500,
            "delta": -200,
            "direction": "lower_is_better",
        },
    }


def test_case_results_v2_adds_review_and_metric_defaults() -> None:
    # Given: a minimal legacy case result.
    evals = [{"input": "Find facts.", "expected": "Cited facts."}]
    raw_case_results = [{"case_index": 0, "status": "failed", "score": 0.3}]

    # When: case results are normalized.
    _summary, _benchmark, case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
    )

    # Then: missing v2 fields get stable null/default values.
    assert case_results == [
        {
            "case_index": 0,
            "name": None,
            "input": "Find facts.",
            "expected": "Cited facts.",
            "status": "failed",
            "score": 0.3,
            "baseline_status": None,
            "baseline_score": None,
            "triggered": None,
            "baseline_triggered": None,
            "duration_ms": None,
            "baseline_duration_ms": None,
            "tokens": None,
            "baseline_tokens": None,
            "error": None,
            "evidence": None,
            "grader_feedback": None,
            "review_status": "unreviewed",
        }
    ]


def test_case_results_v2_preserves_execution_compatibility_fields() -> None:
    # Given: a deterministic script-backed case result with legacy execution data.
    evals = [{"input": "Run the probe.", "expected": "A redacted preview."}]
    raw_case_results = [
        {
            "case_index": 0,
            "status": "passed",
            "score": 1,
            "notes": "Executed through execute_in_skill.",
            "execution": {
                "status": "passed",
                "output_preview": "SECRET=<redacted:OPENAI_API_KEY>",
            },
        }
    ]

    # When: the case result is normalized.
    _summary, _benchmark, case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
    )

    # Then: legacy worker/UI fields remain available next to the v2 fields.
    assert case_results[0]["notes"] == "Executed through execute_in_skill."
    assert case_results[0]["execution"] == {
        "status": "passed",
        "output_preview": "SECRET=<redacted:OPENAI_API_KEY>",
    }


def test_result_schema_accepts_missing_duration_and_token_metrics() -> None:
    # Given: a passed result without runtime metrics.
    evals = [{"input": "Classify.", "expected": "A label."}]
    raw_case_results = [{"case_index": 0, "status": "passed", "score": 0.8}]

    # When: the result is normalized.
    summary, benchmark, _case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
    )

    # Then: metric cards and comparisons remain present with null values.
    kpis = summary["kpis"]
    assert isinstance(kpis, dict)
    assert kpis["average_duration_ms"] == {
        "value": None,
        "baseline_value": None,
        "delta": None,
        "direction": "lower_is_better",
    }
    assert kpis["average_tokens"] == {
        "value": None,
        "baseline_value": None,
        "delta": None,
        "delta_rate": None,
        "direction": "lower_is_better",
    }
    comparison = benchmark["comparison"]
    assert isinstance(comparison, dict)
    assert comparison["duration_ms"]["delta"] is None
    assert comparison["tokens"]["delta"] is None


def test_result_schema_clamps_scores_and_normalizes_statuses() -> None:
    # Given: invalid scores and unknown status values from a grader.
    evals = [{"input": "Parse.", "expected": "JSON."}]
    raw_case_results = [
        {
            "case_index": 0,
            "status": "maybe",
            "score": 2.5,
            "baseline_status": "unknown",
            "baseline_score": -1,
        }
    ]

    # When: the case is normalized.
    _summary, _benchmark, case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
    )

    # Then: scores are clamped and unknown statuses become errors.
    assert case_results[0]["score"] == 1.0
    assert case_results[0]["baseline_score"] == 0.0
    assert case_results[0]["status"] == "error"
    assert case_results[0]["baseline_status"] == "error"
