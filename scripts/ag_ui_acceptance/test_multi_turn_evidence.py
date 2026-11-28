"""Multi-turn evidence must preserve browser outcomes and exclude raw chat data."""

# ruff: noqa: S101 -- pytest assertions.
import base64
import json
from pathlib import Path

import pytest
from evidence import playwright_report
from multi_turn_evidence import TURN_ATTACHMENT, TURN_TITLE

COMMAND = {
    "wire": "ag_ui",
    "strategy": "normal",
    "status": 200,
    "requestSlot": 1,
    "physicalSlot": 1,
    "inputSlot": 1,
}
MESSAGE = {"role": "assistant", "slot": 1, "hasContent": True}
RUN = {"physicalSlot": 1, "status": "completed", "source": "chat"}
RECEIPT = {
    "stage": "both-restored",
    "commands": [COMMAND],
    "messagesStatus": 200,
    "historyStatus": 0,
    "inputsStatus": 404,
    "stored": [MESSAGE],
    "runs": [RUN],
    "items": [{"slot": 1, "physicalSlot": 1, "status": "claimed"}],
    "queuePaused": False,
    "renderedUsers": 2,
    "renderedAssistants": 2,
}


def project(
    root: Path,
    *receipts: dict,
    title: str = TURN_TITLE,
    group: str = "ordinary-regressions-ag_ui",
    filename: str = "chat-interactions.spec.ts",
    passed: bool = True,
) -> dict:
    attachments = [
        {
            "name": TURN_ATTACHMENT,
            "contentType": "application/json",
            "body": base64.b64encode(json.dumps(receipt).encode()).decode(),
        }
        for receipt in receipts
    ]
    attempt = {
        "status": "passed" if passed else "failed",
        "retry": 0,
        "duration": 10,
        "errors": [] if passed else [{}],
        "attachments": attachments,
    }
    case = {
        "file": filename,
        "title": title,
        "tests": [
            {
                "expectedStatus": "passed",
                "status": "expected" if passed else "unexpected",
                "results": [attempt],
            }
        ],
    }
    report = {
        "errors": [],
        "stats": {"expected": int(passed), "unexpected": int(not passed), "flaky": 0, "skipped": 0},
        "suites": [{"specs": [case]}],
    }
    return playwright_report(report, root, group=group, specs=[filename])["cases"][0]


@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize(
    "group", ["ordinary-regressions-ag_ui", "ordinary-regressions-langgraph_v3"]
)
def test_preserves_outcome_and_correlated_ordinals(
    tmp_path: Path, passed: bool, group: str
) -> None:
    case = project(tmp_path, RECEIPT, passed=passed, group=group)
    assert case["passed"] is passed
    assert case["observations"] == {TURN_ATTACHMENT: RECEIPT}


@pytest.mark.parametrize(
    "patch",
    [
        {"stage": "private-value"},
        {"extra": "private-value"},
        {"renderedAssistants": 65},
        {"messagesStatus": False},
        {"historyStatus": 99},
        {"inputsStatus": 600},
        {"queuePaused": "true"},
        {"commands": [COMMAND] * 9},
        {"commands": [COMMAND | {"requestSlot": "private-value"}]},
        {"stored": [MESSAGE | {"content": "private-value"}]},
        {"stored": [MESSAGE] * 17},
        {"runs": [RUN | {"source": "private-value"}]},
        {"items": [{"status": "private-value"}]},
    ],
)
def test_rejects_unknown_unbounded_and_private_data(tmp_path: Path, patch: dict) -> None:
    case = project(tmp_path, RECEIPT | patch)
    assert case["passed"]
    assert case["observations"] == {}
    assert "private-value" not in json.dumps(case)


def test_omits_ambiguous_receipts_and_unrelated_cases(tmp_path: Path) -> None:
    assert project(tmp_path, RECEIPT, RECEIPT)["observations"] == {}
    assert project(tmp_path, RECEIPT, filename="chat-other.spec.ts")["observations"] == {}
    assert project(tmp_path, RECEIPT, title=TURN_TITLE + " again")["observations"] == {}
    assert project(tmp_path, RECEIPT, group="cutover")["observations"] == {}
