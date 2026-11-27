"""Require a bounded, single-attempt proof for the first native approval."""

# ruff: noqa: S101 -- pytest assertions.
import base64
import json
from pathlib import Path

import pytest
from evidence import playwright_report

FILENAME = "ag-ui-approval-readiness.spec.ts"
GROUP = "standard-client-regressions"
TITLE = "submits the first native answer once after the durable approval snapshot arrives"
MIDDLEWARE_TITLE = (
    "submits the first middleware-backed answer once after the durable approval snapshot arrives"
)
TAG = "approval-readiness"
INVALID = "missing_or_invalid_approval_readiness"
INCOMPLETE = "incomplete_approval_readiness"
COMPLETE = {
    "selectedRequests": 2,
    "targetedRequests": 0,
    "interceptedReads": 2,
    "successfulReads": 2,
    "interruptedReads": 1,
    "malformedReads": 0,
    "heldReads": 1,
    "lastHttpStatus": 200,
    "terminalReason": "ask_user",
    "lastSnapshotReason": "ask_user",
    "sameThread": True,
    "sameRun": True,
    "sameSlot": True,
    "sameActionContract": True,
    "samePhysicalRun": True,
    "hasCheckpoint": True,
    "actionable": True,
    "lockedBeforeRelease": True,
    "effectVerified": True,
    "resumePostsBeforeRelease": 0,
    "resumePosts": 1,
    "completedResumeRuns": 1,
    "savedAnswers": 1,
}


def attachment(data: object) -> dict:
    return {
        "name": TAG,
        "contentType": "application/json",
        "body": base64.b64encode(json.dumps(data).encode()).decode(),
    }


def summary(
    root: Path,
    *attachments: dict,
    filename: str = FILENAME,
    title: str = TITLE,
    group: str = GROUP,
    statuses: tuple[str, ...] = ("passed",),
    whole: bool = False,
    companion: bool = False,
) -> dict:
    attempts = [
        {
            "status": s,
            "retry": i,
            "duration": 42,
            "attachments": list(attachments),
            "errors": [] if s == "passed" else [{}],
        }
        for i, s in enumerate(statuses)
    ]
    outcome = (
        ("flaky" if len(attempts) > 1 else "expected") if statuses[-1] == "passed" else "unexpected"
    )
    spec = {
        "file": filename,
        "title": title,
        "line": 12,
        "tests": [{"expectedStatus": "passed", "status": outcome, "results": attempts}],
    }
    specs = [spec]
    if companion:
        proof = COMPLETE | {
            "terminalReason": "tool_approval",
            "lastSnapshotReason": "tool_approval",
        }
        other = attempts[0] | {"attachments": [attachment(proof)]}
        specs.append(
            spec
            | {
                "title": MIDDLEWARE_TITLE,
                "line": 19,
                "tests": [spec["tests"][0] | {"results": [other]}],
            }
        )
    report = {
        "errors": [],
        "suites": [{"specs": specs}],
        "stats": {
            key: len(specs) if key == outcome else 0
            for key in ("expected", "unexpected", "skipped", "flaky")
        },
    }
    selector = filename if whole else filename + ":12"
    return playwright_report(report, root, specs=[selector], group=group)


def case(root: Path, *attachments: dict, **kwargs) -> dict:
    result = summary(root, *attachments, **kwargs)
    assert result["passed"] == result["cases"][0]["passed"]
    return result["cases"][0]


@pytest.mark.parametrize(
    ("title", "reason"), [(TITLE, "ask_user"), (MIDDLEWARE_TITLE, "tool_approval")]
)
def test_each_first_answer_requires_its_own_durable_contract(
    tmp_path: Path, title: str, reason: str
) -> None:
    receipt = COMPLETE | {"terminalReason": reason, "lastSnapshotReason": reason}
    observed = case(tmp_path, attachment(receipt), title=title)
    assert observed["passed"]
    assert observed["observations"] == {TAG: receipt}
    other = TITLE if title == MIDDLEWARE_TITLE else MIDDLEWARE_TITLE
    assert INCOMPLETE in case(tmp_path, attachment(receipt), title=other)["errors"]


@pytest.mark.parametrize("field", [key for key, value in COMPLETE.items() if value is True])
def test_missing_positive_signal_preserves_finite_diagnostics(tmp_path: Path, field: str) -> None:
    partial = COMPLETE | {field: False}
    observed = case(tmp_path, attachment(partial))
    assert INCOMPLETE in observed["errors"]
    assert observed["observations"] == {TAG: partial}


@pytest.mark.parametrize(
    "patch",
    [
        {"selectedRequests": 0},
        {"interceptedReads": 0},
        {"successfulReads": 0},
        {"interruptedReads": 0},
        {"heldReads": 0},
        {"resumePostsBeforeRelease": 1},
        {"resumePosts": 0},
        {"resumePosts": 2},
        {"completedResumeRuns": 2},
        {"savedAnswers": 0},
        {"terminalReason": "tool_approval"},
        {"lastSnapshotReason": "input_required"},
        {"lastHttpStatus": 403},
    ],
)
def test_rejects_unproven_single_action(tmp_path: Path, patch: dict) -> None:
    assert INCOMPLETE in case(tmp_path, attachment(COMPLETE | patch))["errors"]


@pytest.mark.parametrize(
    "bad",
    [
        {"private-field": "synthetic-unapproved-value"},
        {"lastSnapshotReason": "synthetic-unapproved-value"},
        {"lastHttpStatus": 99},
        {"heldReads": True},
        {"sameSlot": 1},
        {"resumePosts": 201},
        {"sameRun": "true"},
        {"successfulReads": float("inf")},
    ],
)
def test_rejects_malformed_or_private_data(tmp_path: Path, bad: dict) -> None:
    observed = case(tmp_path, attachment(COMPLETE | bad))
    assert INVALID in observed["errors"]
    assert not observed["observations"]
    assert "synthetic-unapproved-value" not in json.dumps(observed)


def test_only_a_single_valid_json_attachment_can_count(tmp_path: Path) -> None:
    valid = attachment(COMPLETE)
    for values in (
        (),
        (valid, valid),
        (valid | {"contentType": "text/plain"},),
        (valid | {"body": "not-base64"},),
        (attachment([]),),
        (attachment({}),),
    ):
        assert INVALID in case(tmp_path, *values)["errors"]


@pytest.mark.parametrize(
    "scope",
    [
        {"filename": "another.spec.ts"},
        {"title": TITLE + " on reload"},
        {"group": "ordinary-regressions-ag_ui"},
    ],
)
def test_receipt_is_ignored_outside_its_exact_scope(tmp_path: Path, scope: dict) -> None:
    observed = case(tmp_path, attachment(COMPLETE), **scope)
    assert observed["passed"]
    assert not observed["observations"]


@pytest.mark.parametrize("statuses", [("failed",), ("failed", "passed")])
def test_receipt_cannot_turn_a_failed_or_retried_browser_run_green(
    tmp_path: Path,
    statuses: tuple[str, ...],
) -> None:
    observed = case(tmp_path, attachment(COMPLETE), statuses=statuses)
    assert "requires_one_first_attempt_pass" in observed["errors"]


@pytest.mark.parametrize(
    ("title", "reason"), [(TITLE, "ask_user"), (MIDDLEWARE_TITLE, "tool_approval")]
)
def test_whole_spec_must_execute_both_cases(tmp_path: Path, title: str, reason: str) -> None:
    proof = COMPLETE | {"terminalReason": reason, "lastSnapshotReason": reason}
    result = summary(tmp_path, attachment(proof), title=title, whole=True)
    assert not result["passed"]
    assert result["cases"][0]["passed"]
    assert "missing_approval_readiness_case" in result["errors"]


def test_whole_file_with_both_verified_cases_passes(tmp_path: Path) -> None:
    result = summary(tmp_path, attachment(COMPLETE), whole=True, companion=True)
    assert result["passed"]
    assert result["counts"]["verified"] == 2
