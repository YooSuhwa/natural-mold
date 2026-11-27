"""Regression checks for the pinned Playwright JSON report projection."""

# ruff: noqa: S101 -- pytest assertion statements.
import base64
import json
from pathlib import Path

import pytest
from evidence import PERF_TITLE, cutover_attachments, playwright_report


@pytest.fixture
def report() -> dict:
    annotations = [{"type": "perf", "description": "TTFT=100ms total=3100ms spread=3000ms"}]
    attempt = {
        "retry": 0,
        "duration": 3180,
        "status": "passed",
        "errors": [],
        "annotations": list(annotations),
        "attachments": [],
    }
    test = {
        "expectedStatus": "passed",
        "status": "expected",
        "results": [attempt],
        "annotations": list(annotations),
    }
    return {
        "errors": [],
        "stats": {"expected": 1, "unexpected": 0, "skipped": 0, "flaky": 0},
        "suites": [{"specs": [{"file": "chat.spec.ts", "title": PERF_TITLE, "tests": [test]}]}],
    }


def test_time_measurements_must_be_present_on_the_attempt(report: dict, tmp_path: Path) -> None:
    args = {"group": "performance-ag_ui", "specs": ["chat.spec.ts"]}
    summary = playwright_report(report, tmp_path, **args)
    assert summary["passed"]
    assert summary["cases"][0]["attempts"][0]["perf"] == {
        "ttft_ms": 100,
        "total_ms": 3100,
        "spread_ms": 3000,
    }
    report["suites"][0]["specs"][0]["tests"][0]["results"][0]["annotations"] = []
    assert not playwright_report(report, tmp_path, **args)["passed"]


def test_missing_file_even_when_another_file_passes(report: dict, tmp_path: Path) -> None:
    summary = playwright_report(
        report,
        tmp_path,
        group="ordinary-regressions-ag_ui",
        specs=["frontend/e2e/chat.spec.ts", "e2e/second.spec.ts"],
    )
    assert "missing_or_unexpected_spec" in summary["errors"]


def test_expected_failure_keeps_only_public_error_coordinates(report: dict, tmp_path: Path) -> None:
    test = report["suites"][0]["specs"][0]["tests"][0]
    attempt = test["results"][0]
    test["expectedStatus"] = attempt["status"] = "failed"
    attempt["error"] = {
        "message": (
            "Error: expect(received).toBe(expected)\n"
            'Expected: "unused"\nReceived: "private-assertion-text"'
        ),
        "location": {"file": str(tmp_path / "e2e/chat.spec.ts"), "line": 10, "column": 4},
    }
    attempt["stdout"] = [{"text": "private-assertion-text"}]
    summary = playwright_report(
        report, tmp_path, group="ordinary-regressions-ag_ui", specs=["chat.spec.ts"]
    )
    assert not summary["passed"]
    assert summary["counts"]["verified"] == 0
    assert summary["cases"][0]["attempts"][0]["error_source"]["line"] == 10
    assert summary["cases"][0]["attempts"][0]["assertion"] == {
        "matcher": "toBe",
        "expected_type": "string",
        "received_type": "string",
    }
    assert "private-assertion-text" not in json.dumps(summary)


def test_attachment_has_typed_fields_and_requires_owned_files(tmp_path: Path) -> None:
    marker = "private-assertion-text"
    payload = {
        "conversationId": "conv",
        "goalId": "goal",
        "runId": "run",
        "goalRevision": 3,
        "steerId": "steer",
        "movedInputId": "in-1",
        "nextInputId": "in-2",
        "physicalRuns": [
            {"id": "id", "parent_run_id": None, "status": "running", "private": marker}
        ],
        "newPrivateField": marker,
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    attachment = {
        "name": "steer-and-goal-cutover",
        "contentType": "application/json",
        "body": encoded,
    }
    result = {"attachments": [attachment]}
    exported = cutover_attachments([result], tmp_path)[attachment["name"]]
    assert exported["conversationId"].startswith("sha256:")
    assert marker not in json.dumps(exported)
    attachment.pop("body")
    attachment["path"] = str(tmp_path.parent / "unowned.json")
    with pytest.raises(ValueError, match="Unexpected cutover attachment path"):
        cutover_attachments([result], tmp_path)
