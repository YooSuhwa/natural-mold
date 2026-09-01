"""Child-receipt validation contracts for canonical project-gate waves."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.project_gate_wave_support import JSONObject, JSONValue, load_module


@pytest.fixture(scope="module")
def receipts() -> ModuleType:
    return load_module("project_gate_receipts")


def test_static_receipt_rejects_cleanup_failure(tmp_path: Path, receipts: ModuleType) -> None:
    """Given a child with residue, when parsed, then cleanup failure rejects the receipt."""
    path = tmp_path / "static.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "child_exit_code": 0,
                "cleanup": "cleanup_failed",
                "run_root_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(receipts.ProjectGateError, match="invalid_child_receipt"):
        receipts.validate_static(path, tmp_path, 0)


def test_postgres_receipt_rejects_skip_or_deselection(tmp_path: Path, receipts: ModuleType) -> None:
    """Given PostgreSQL selection debt, when parsed, then the canonical acceptance fails."""
    scenario: JSONObject = {
        "scenario": "all",
        "status": "passed",
        "child_exit_code": 0,
        "test_receipt": {
            "selected_node_ids": ["a", "b"],
            "executed_node_ids": ["a"],
            "failed_node_ids": [],
            "skipped_node_ids": ["b"],
            "deselected_node_ids": [],
        },
    }
    scenario.update(dict.fromkeys(receipts.POSTGRES_CLEANUP_KEYS, True))
    path = tmp_path / "postgres.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "all",
                "status": "passed",
                "scenarios": [scenario],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(receipts.ProjectGateError, match="invalid_child_receipt"):
        receipts.validate_postgres(path, tmp_path, "all", 0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retries", 1),
        ("selected_ids", ["a", "b"]),
        ("export", {"secret_scan_passed": False}),
    ],
)
def test_e2e_receipt_rejects_retry_selection_or_secret_debt(
    tmp_path: Path, receipts: ModuleType, field: str, value: JSONValue
) -> None:
    """Given E2E retry/selection/secret debt, when parsed, then the wave cannot pass."""
    cleanup: JSONObject = dict.fromkeys(receipts.E2E_CLEANUP_KEYS, True)
    payload: JSONObject = {
        "runner": "moldy-isolated-e2e",
        "lane": "scripted",
        "project": "scripted-full",
        "workers": 1,
        "retries": 0,
        "status": "passed",
        "child_exit_code": 0,
        "selected_ids": ["scripted-full::e2e/a.spec.ts::works"],
        "executed_ids": ["scripted-full::e2e/a.spec.ts::works"],
        "unexpected_failures": [],
        "export": {"secret_scan_passed": True, "screenshots": []},
        "cleanup": cleanup,
    }
    payload[field] = value
    path = tmp_path / f"e2e-{field}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(receipts.ProjectGateError, match="invalid_child_receipt"):
        receipts.validate_e2e(
            path,
            tmp_path,
            0,
            project="scripted-full",
            expected_spec=None,
        )


def test_e2e_receipt_accepts_failed_child_with_sanitized_export(
    tmp_path: Path, receipts: ModuleType
) -> None:
    node = "scripted-full::e2e/chat-error-retry.spec.ts::retries failed run"
    payload: JSONObject = {
        "runner": "moldy-isolated-e2e",
        "lane": "scripted",
        "project": "scripted-full",
        "workers": 1,
        "retries": 0,
        "status": "failed",
        "child_exit_code": 1,
        "selected_ids": [node],
        "executed_ids": [node],
        "unexpected_failures": [
            {
                "node_id": node,
                "status": "failed",
                "location": {
                    "file": "e2e/chat-error-retry.spec.ts",
                    "line": 49,
                    "column": 6,
                },
                "network_failure_codes": [
                    "api_request_failure",
                    "other_response_failure",
                ],
                "failure_phase": "verify_error_collectors",
            }
        ],
        "export": {"secret_scan_passed": True, "screenshots": []},
        "cleanup": dict.fromkeys(receipts.E2E_CLEANUP_KEYS, True),
    }
    path = tmp_path / "e2e-failed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = receipts.validate_e2e(
        path,
        tmp_path,
        1,
        project="scripted-full",
        expected_spec=None,
    )

    assert summary["secret_scan_passed"] is True


def test_e2e_receipt_rejects_failed_child_without_failure_diagnostics(
    tmp_path: Path, receipts: ModuleType
) -> None:
    node = "scripted-full::e2e/chat-error-retry.spec.ts::retries failed run"
    payload: JSONObject = {
        "runner": "moldy-isolated-e2e",
        "lane": "scripted",
        "project": "scripted-full",
        "workers": 1,
        "retries": 0,
        "status": "failed",
        "child_exit_code": 1,
        "selected_ids": [node],
        "executed_ids": [node],
        "export": {"secret_scan_passed": True, "screenshots": []},
        "cleanup": dict.fromkeys(receipts.E2E_CLEANUP_KEYS, True),
    }
    path = tmp_path / "e2e-failed-without-diagnostics.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(receipts.ProjectGateError, match="invalid_child_receipt"):
        receipts.validate_e2e(
            path,
            tmp_path,
            1,
            project="scripted-full",
            expected_spec=None,
        )


def test_e2e_receipt_accepts_legacy_success_without_failure_diagnostics(
    tmp_path: Path, receipts: ModuleType
) -> None:
    node = "scripted-full::e2e/a.spec.ts::works"
    payload: JSONObject = {
        "runner": "moldy-isolated-e2e",
        "lane": "scripted",
        "project": "scripted-full",
        "workers": 1,
        "retries": 0,
        "status": "passed",
        "child_exit_code": 0,
        "selected_ids": [node],
        "executed_ids": [node],
        "export": {"secret_scan_passed": True, "screenshots": []},
        "cleanup": dict.fromkeys(receipts.E2E_CLEANUP_KEYS, True),
    }
    path = tmp_path / "e2e-legacy-success.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = receipts.validate_e2e(
        path,
        tmp_path,
        0,
        project="scripted-full",
        expected_spec=None,
    )

    assert summary["secret_scan_passed"] is True


@pytest.mark.parametrize(
    "location_file",
    ["/tmp/e2e/chat-error-retry.spec.ts", "e2e/other-safe.spec.ts"],
)
def test_e2e_receipt_rejects_untrusted_failure_location(
    tmp_path: Path, receipts: ModuleType, location_file: str
) -> None:
    node = "scripted-full::e2e/chat-error-retry.spec.ts::retries failed run"
    payload: JSONObject = {
        "runner": "moldy-isolated-e2e",
        "lane": "scripted",
        "project": "scripted-full",
        "workers": 1,
        "retries": 0,
        "status": "failed",
        "child_exit_code": 1,
        "selected_ids": [node],
        "executed_ids": [node],
        "unexpected_failures": [
            {
                "node_id": node,
                "status": "failed",
                "location": {
                    "file": location_file,
                    "line": 49,
                    "column": 6,
                },
            }
        ],
        "export": {"secret_scan_passed": True, "screenshots": []},
        "cleanup": dict.fromkeys(receipts.E2E_CLEANUP_KEYS, True),
    }
    path = tmp_path / "e2e-unsafe-location.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(receipts.ProjectGateError, match="invalid_child_receipt"):
        receipts.validate_e2e(
            path,
            tmp_path,
            1,
            project="scripted-full",
            expected_spec=None,
        )


def test_capture_receipt_requires_exact_safe_screenshot_contract(
    tmp_path: Path, receipts: ModuleType
) -> None:
    """Given the visual capture lane, when parsed, then all 13 safe PNGs are required."""
    spec = "e2e/chat-langgraph-v3-visual-matrix.spec.ts"
    selected = [f"scripted-capture::{spec}::viewport-{index}" for index in range(3)]
    selected_json: list[JSONValue] = []
    selected_json.extend(selected)
    screenshots: list[JSONValue] = []
    screenshots.extend(f"captures/{index:02d}.png" for index in range(13))
    payload: JSONObject = {
        "runner": "moldy-isolated-e2e",
        "lane": "scripted",
        "project": "scripted-capture",
        "workers": 1,
        "retries": 0,
        "status": "passed",
        "child_exit_code": 0,
        "selected_ids": selected_json,
        "executed_ids": selected_json,
        "unexpected_failures": [],
        "export": {
            "secret_scan_passed": True,
            "screenshots": screenshots,
        },
        "cleanup": dict.fromkeys(receipts.E2E_CLEANUP_KEYS, True),
    }
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = receipts.validate_e2e(
        path,
        tmp_path,
        0,
        project="scripted-capture",
        expected_spec=spec,
    )

    assert summary["screenshot_count"] == 13
