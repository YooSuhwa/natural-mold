"""Strict scalar failure-phase diagnostics for isolated E2E receipts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_failure_diagnostics import (  # noqa: E402
    FailureDiagnostic,
    FailureDiagnosticError,
    SourceRejection,
    failure_diagnostics_payload,
    parse_failure_diagnostics,
    parse_source_rejection,
    source_rejection_payload,
)
from e2e_runner_failure_phase import (  # noqa: E402
    FAILURE_PHASE_ANNOTATION_TYPE,
    FailurePhase,
    FailurePhaseError,
    extract_failure_phase,
    parse_failure_phase,
)
from e2e_runner_playwright import (  # noqa: E402
    PlaywrightReceiptError,
    parse_playwright_execution_json,
)


def _receipt(*, expected: str, status: str, annotations: object) -> dict[str, object]:
    return {
        "suites": [
            {
                "file": "e2e/chat-transcript-stability.spec.ts",
                "specs": [
                    {
                        "title": "keeps the user prompt stable",
                        "tests": [
                            {
                                "projectName": "scripted-full",
                                "expectedStatus": expected,
                                "results": [
                                    {
                                        "status": status,
                                        "annotations": annotations,
                                        "error": {
                                            "message": "api_key=must-not-survive",
                                            "stack": "Bearer must-not-survive",
                                        },
                                        "stdout": ["must-not-survive"],
                                        "stderr": ["must-not-survive"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_failure_phase_enum_round_trips_through_annotation_and_persistence() -> None:
    for phase in FailurePhase:
        result = {
            "annotations": [{"type": FAILURE_PHASE_ANNOTATION_TYPE, "description": phase.value}]
        }

        assert extract_failure_phase(result) is phase
        assert parse_failure_phase(phase.value) is phase
        parsed = parse_failure_diagnostics(
            [
                {
                    "node_id": "scripted-full::e2e/a.spec.ts::fails",
                    "status": "failed",
                    "failure_phase": phase.value,
                }
            ],
            "scripted-full",
        )
        assert parsed[0].failure_phase is phase
        assert failure_diagnostics_payload(parsed, "scripted-full")[0].get("failure_phase") is phase


def test_failure_phase_extraction_absent_or_unrelated_is_none() -> None:
    assert extract_failure_phase({}) is None
    assert extract_failure_phase({"annotations": []}) is None
    assert (
        extract_failure_phase({"annotations": [{"type": "unrelated", "description": "ignored"}]})
        is None
    )


@pytest.mark.parametrize(
    "result",
    [
        {"annotations": "https://user:password@example.test/?api_key=must-not-survive"},
        {"annotations": [{"type": FAILURE_PHASE_ANNOTATION_TYPE}]},
        {
            "annotations": [
                {
                    "type": FAILURE_PHASE_ANNOTATION_TYPE,
                    "description": "complete",
                    "raw": "must-not-survive",
                }
            ]
        },
        {
            "annotations": [
                {
                    "type": FAILURE_PHASE_ANNOTATION_TYPE,
                    "description": "must-not-survive",
                }
            ]
        },
        {"annotations": [{"type": FAILURE_PHASE_ANNOTATION_TYPE, "description": 500}]},
        {
            "annotations": [
                {"type": FAILURE_PHASE_ANNOTATION_TYPE, "description": "complete"},
                {"type": FAILURE_PHASE_ANNOTATION_TYPE, "description": "complete"},
            ]
        },
        {
            "annotations": [
                {"type": FAILURE_PHASE_ANNOTATION_TYPE, "description": "complete"},
                {
                    "type": FAILURE_PHASE_ANNOTATION_TYPE,
                    "description": "must-not-survive",
                },
            ]
        },
    ],
)
def test_failure_phase_extraction_rejects_malformed_reserved_data_without_reflection(
    result: object,
) -> None:
    with pytest.raises(FailurePhaseError, match="invalid_failure_phase") as error:
        extract_failure_phase(result)

    assert "must-not-survive" not in repr(error.value)


@pytest.mark.parametrize("phase", ["must-not-survive", 500, None])
def test_persisted_failure_phase_rejects_non_enum_values_without_reflection(
    phase: object,
) -> None:
    value = [
        {
            "node_id": "scripted-full::e2e/a.spec.ts::fails",
            "status": "failed",
            "failure_phase": phase,
        }
    ]

    with pytest.raises(FailureDiagnosticError, match="invalid_source_rejection") as error:
        parse_failure_diagnostics(value, "scripted-full")

    assert "must-not-survive" not in repr(error.value)


def test_timed_out_unexpected_playwright_result_projects_only_the_finite_phase(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "execution.json"
    receipt.write_text(
        json.dumps(
            _receipt(
                expected="passed",
                status="timedOut",
                annotations=[
                    {
                        "type": FAILURE_PHASE_ANNOTATION_TYPE,
                        "description": "wait_ask_user_card",
                    }
                ],
            )
        )
    )

    actual = parse_playwright_execution_json(receipt).unexpected_outcomes

    assert len(actual) == 1
    assert actual[0].status == "timedOut"
    assert actual[0].failure_phase is FailurePhase.WAIT_ASK_USER_CARD
    assert "must-not-survive" not in repr(actual)


@pytest.mark.parametrize(("expected", "status"), [("passed", "passed"), ("failed", "failed")])
def test_passes_and_expected_failures_do_not_project_failure_phase(
    tmp_path: Path, expected: str, status: str
) -> None:
    receipt = tmp_path / "execution.json"
    receipt.write_text(
        json.dumps(
            _receipt(
                expected=expected,
                status=status,
                annotations=[{"type": FAILURE_PHASE_ANNOTATION_TYPE, "description": "complete"}],
            )
        )
    )

    assert parse_playwright_execution_json(receipt).unexpected_outcomes == ()


def test_playwright_parser_converts_invalid_reserved_phase_to_stable_receipt_error(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "execution.json"
    receipt.write_text(
        json.dumps(
            _receipt(
                expected="passed",
                status="failed",
                annotations=[
                    {
                        "type": FAILURE_PHASE_ANNOTATION_TYPE,
                        "description": "must-not-survive",
                    }
                ],
            )
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="invalid_failure_phase") as error:
        parse_playwright_execution_json(receipt)

    assert "must-not-survive" not in repr(error.value)


def test_source_rejection_never_accepts_or_serializes_failure_phase() -> None:
    source_rejection = {
        "category": "secret_scan",
        "rule_id": "bearer_token",
        "artifact_path": "results/execution.log",
        "tests": [
            {
                "node_id": "scripted-full::e2e/a.spec.ts::fails",
                "status": "failed",
                "failure_phase": "complete",
            }
        ],
    }

    with pytest.raises(FailureDiagnosticError, match="invalid_source_rejection"):
        parse_source_rejection(source_rejection, "scripted-full")

    payload = source_rejection_payload(
        SourceRejection(
            "secret_scan",
            "bearer_token",
            "results/execution.log",
            (FailureDiagnostic("scripted-full::e2e/a.spec.ts::fails", "failed"),),
        )
    )
    assert payload["tests"] == [
        {"node_id": "scripted-full::e2e/a.spec.ts::fails", "status": "failed"}
    ]
    assert "failure_phase" not in repr(payload)
