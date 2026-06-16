from __future__ import annotations

import json

from app.services.skill_evaluation_result_schema import normalize_skill_evaluation_result


def test_result_schema_drops_non_finite_numbers() -> None:
    # Given: a grader result containing Python-parsed NaN and infinity values.
    evals = [{"input": "Parse.", "expected": "JSON."}]
    raw_case_results = [
        {
            "case_index": 0,
            "score": float("nan"),
            "baseline_score": float("inf"),
            "duration_ms": float("inf"),
            "baseline_duration_ms": float("-inf"),
            "tokens": float("nan"),
        }
    ]
    raw_benchmark = {"without_skill_pass_rate": float("nan")}
    raw_benchmark["with_skill_mean_score"] = float("nan")
    raw_benchmark["with_skill_stddev_score"] = float("inf")
    raw_benchmark["comparison"] = {"pass_rate": {"delta": float("-inf")}}

    # When: the result is normalized.
    summary, benchmark, case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
        raw_benchmark=raw_benchmark,
    )

    # Then: non-finite values cannot leak into stored JSON or pass the case.
    assert case_results[0]["score"] == 0.0
    assert case_results[0]["baseline_score"] is None
    assert case_results[0]["status"] == "failed"
    assert case_results[0]["duration_ms"] is None
    assert summary["average_duration_ms"] is None
    assert benchmark["without_skill_pass_rate"] is None
    assert benchmark["with_skill_mean_score"] is None
    assert benchmark["with_skill_stddev_score"] is None
    assert benchmark["comparison"]["pass_rate"]["delta"] is None
    json.dumps(summary, allow_nan=False)
    json.dumps(benchmark, allow_nan=False)
    json.dumps(case_results, allow_nan=False)


def test_trigger_accuracy_kpi_uses_case_count_as_total() -> None:
    # Given: only one of two cases reports trigger data.
    evals = [
        {"input": "A", "expected": "A"},
        {"input": "B", "expected": "B"},
    ]
    raw_case_results = [
        {"case_index": 0, "status": "passed", "score": 1, "triggered": True},
        {"case_index": 1, "status": "passed", "score": 1},
    ]

    # When: KPI summary is normalized.
    summary, _benchmark, _case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=raw_case_results,
    )

    # Then: the KPI denominator remains the full case count.
    kpis = summary["kpis"]
    assert isinstance(kpis, dict)
    assert kpis["trigger_accuracy"] == {
        "value": 0.5,
        "passed": 1,
        "total": 2,
        "delta": None,
        "direction": "higher_is_better",
    }
