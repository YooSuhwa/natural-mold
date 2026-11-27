"""Failure diagnostics must not change the ordinary browser verdict or retain arbitrary text."""

# ruff: noqa: S101 -- pytest assertions.
import base64
import json
from pathlib import Path

import pytest
from evidence import playwright_report

FILENAME = "hitl-approval.spec.ts"
TITLE = "rejecting an execute_in_skill approval skips the tool"
GROUP = "ordinary-regressions-ag_ui"
RECEIPT = {
    "schemaVersion": 1,
    "postCount": 1,
    "postStatus": 200,
    "terminal": "interrupt",
    "terminalSlotCount": 1,
    "terminalApprovalCount": 1,
    "snapshotCount": 2,
    "snapshotStatus": 200,
    "durable": "idle",
    "durableSlotCount": 0,
    "durableApprovalCount": 0,
    "durableReadOnlyCount": 0,
    "sameSlots": False,
    "sameRun": None,
    "hasCheckpoint": True,
    "grantReadStatus": None,
    "approveVisible": False,
    "grantVisible": False,
}


def _summary(tmp_path: Path, data: dict, *, status: str = "failed", title: str = TITLE) -> dict:
    attachment = {
        "name": "approval-card-observations",
        "contentType": "application/json",
        "body": base64.b64encode(json.dumps(data).encode()).decode(),
    }
    passed = status == "passed"
    result = {
        "retry": 0,
        "duration": 10,
        "status": status,
        "attachments": [attachment],
        "errors": [] if passed else [{}],
    }
    test = {
        "expectedStatus": "passed",
        "status": "expected" if passed else "unexpected",
        "results": [result],
    }
    report = {
        "errors": [],
        "stats": {"expected": int(passed), "unexpected": int(not passed), "flaky": 0, "skipped": 0},
        "suites": [{"specs": [{"file": FILENAME, "title": title, "tests": [test]}]}],
    }
    return playwright_report(report, tmp_path, group=GROUP, specs=[FILENAME])


def test_preserves_only_the_target_failure(tmp_path: Path) -> None:
    summary = _summary(tmp_path, RECEIPT)
    assert not summary["passed"]
    assert summary["cases"][0]["observations"] == {"approval-card-observations": RECEIPT}
    passed = _summary(tmp_path, RECEIPT, status="passed")
    assert passed["passed"]
    assert not passed["cases"][0]["observations"]
    assert not _summary(tmp_path, RECEIPT, title="another case")["cases"][0]["observations"]


@pytest.mark.parametrize(
    "bad",
    [
        {"raw": "private-value"},
        {"terminal": "private-value"},
        {"postStatus": 9999},
        {"postCount": True},
    ],
)
def test_discards_unrecognized_data_without_masking_failure(tmp_path: Path, bad: dict) -> None:
    summary = _summary(tmp_path, {**RECEIPT, **bad})
    assert not summary["passed"]
    assert not summary["cases"][0]["observations"]
    assert "private-value" not in json.dumps(summary)
