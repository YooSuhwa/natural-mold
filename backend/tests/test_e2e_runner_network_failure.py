"""Strict enum-only Playwright network failure diagnostic contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_failure_diagnostics import (  # noqa: E402
    FailureDiagnosticError,
    parse_failure_diagnostics,
)
from e2e_runner_network_failure import (  # noqa: E402
    NETWORK_FAILURE_ANNOTATION_TYPE,
    NETWORK_FAILURE_CODES,
    NetworkFailureCodeError,
    extract_network_failure_codes,
    parse_network_failure_codes,
)


def test_result_annotations_extract_all_known_codes_sorted_and_deduplicated() -> None:
    annotations = [
        {"type": NETWORK_FAILURE_ANNOTATION_TYPE, "description": code}
        for code in sorted(NETWORK_FAILURE_CODES, reverse=True)
    ]
    annotations.append(annotations[0])

    actual = extract_network_failure_codes({"annotations": annotations})

    assert actual == tuple(sorted(NETWORK_FAILURE_CODES))


def test_extraction_ignores_non_result_malformed_unknown_and_secret_annotations() -> None:
    raw_secret = "https://user:password@example.test/private?api_key=must-not-survive"
    result = {
        "annotations": [
            raw_secret,
            {"type": "moldy.network-failure.v1.extra", "description": raw_secret},
            {"type": NETWORK_FAILURE_ANNOTATION_TYPE, "description": raw_secret},
            {"type": NETWORK_FAILURE_ANNOTATION_TYPE, "description": 500},
            {"type": NETWORK_FAILURE_ANNOTATION_TYPE, "raw": raw_secret},
            {
                "type": NETWORK_FAILURE_ANNOTATION_TYPE,
                "description": "api_request_failure",
                "raw": raw_secret,
            },
            {
                "type": NETWORK_FAILURE_ANNOTATION_TYPE,
                "description": "other_response_failure",
            },
        ]
    }

    actual = extract_network_failure_codes(result)

    assert actual == ("other_response_failure",)
    assert "must-not-survive" not in repr(actual)
    assert extract_network_failure_codes({"test": {"annotations": result["annotations"]}}) == ()


def test_failure_diagnostic_parser_accepts_only_canonical_optional_code_list() -> None:
    value = [
        {
            "node_id": "scripted-full::e2e/a.spec.ts::fails",
            "status": "failed",
            "network_failure_codes": ["api_request_failure", "other_response_failure"],
        }
    ]

    actual = parse_failure_diagnostics(value, "scripted-full")

    assert actual[0].network_failure_codes == (
        "api_request_failure",
        "other_response_failure",
    )


@pytest.mark.parametrize(
    "codes",
    ["api_request_failure", ["unknown"], ["api_request_failure", "extra"]],
)
def test_failure_diagnostic_parser_rejects_untrusted_optional_code_list(codes: object) -> None:
    value = [
        {
            "node_id": "scripted-full::e2e/a.spec.ts::fails",
            "status": "failed",
            "network_failure_codes": codes,
        }
    ]

    with pytest.raises(FailureDiagnosticError, match="invalid_source_rejection"):
        parse_failure_diagnostics(value, "scripted-full")


@pytest.mark.parametrize(
    "value",
    [
        "api_request_failure",
        ["unknown"],
        ["api_request_failure", "extra"],
        ["api_request_failure", "api_request_failure"],
        ["other_request_failure", "api_request_failure"],
        [],
    ],
)
def test_persisted_code_parser_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(NetworkFailureCodeError, match="invalid_network_failure_codes"):
        parse_network_failure_codes(value)
