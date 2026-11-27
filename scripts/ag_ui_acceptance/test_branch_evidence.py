"""Check that optional Playwright branch diagnostics are bounded and scoped."""

# ruff: noqa: S101 -- pytest assertion statements.
import base64
import json
from pathlib import Path

import pytest
from evidence import playwright_report

TAG = "chat-branch-observation"
REGENERATE = "regenerating an assistant reply forks a sibling branch and the picker navigates"
EDIT = "editing a user message forks a new branch"
FIELDS = {
    "stage": 8,
    "sendStatus": 200,
    "editStatus": 0,
    "regenerateStatus": 599,
    "snapshotCount": 64,
    "siblingCount": 2,
    "selectedLeafListed": True,
    "hasCheckpoint": False,
}


def attachment(body: dict) -> dict:
    encoded = base64.b64encode(json.dumps(body).encode()).decode()
    return {"name": TAG, "contentType": "application/json", "body": encoded}


def project(
    root: Path,
    attachments: list[dict],
    *,
    title: str = REGENERATE,
    filename: str = "chat-interactions.spec.ts",
    group: str = "ordinary-regressions-ag_ui",
    passed: bool = True,
) -> dict:
    attempt = {
        "retry": 0,
        "duration": 15,
        "status": "passed" if passed else "failed",
        "errors": [] if passed else [{}],
        "attachments": attachments,
    }
    test = {
        "expectedStatus": "passed",
        "status": "expected" if passed else "unexpected",
        "results": [attempt],
    }
    summary = playwright_report(
        {
            "errors": [],
            "stats": {
                "expected": int(passed),
                "unexpected": int(not passed),
                "skipped": 0,
                "flaky": 0,
            },
            "suites": [{"specs": [{"file": filename, "title": title, "tests": [test]}]}],
        },
        root,
        group=group,
        specs=[filename],
    )
    return summary["cases"][0]


@pytest.mark.parametrize(("title", "passed"), [(EDIT, True), (REGENERATE, False)])
@pytest.mark.parametrize(
    "group", ["ordinary-regressions-ag_ui", "ordinary-regressions-langgraph_v3"]
)
def test_attachment_preserves_outcome(tmp_path: Path, title: str, passed: bool, group: str) -> None:
    case = project(tmp_path, [attachment(FIELDS)], title=title, passed=passed, group=group)
    assert case["passed"] is passed
    assert case["observations"] == {TAG: FIELDS}


@pytest.mark.parametrize(
    "patch",
    [
        {"stage": 9},
        {"sendStatus": 99},
        {"editStatus": 600},
        {"regenerateStatus": True},
        {"snapshotCount": -1},
        {"siblingCount": 65},
        {"selectedLeafListed": "true"},
        {"hasCheckpoint": 1},
        {"extra": "private-request-content"},
    ],
)
def test_invalid_observation_is_dropped(tmp_path: Path, patch: dict) -> None:
    case = project(tmp_path, [attachment(FIELDS | patch)])
    assert case["observations"] == {}
    assert case["passed"]
    assert "private-request-content" not in json.dumps(case)


@pytest.mark.parametrize(
    "attachments",
    [
        [],
        [attachment(FIELDS), attachment(FIELDS)],
        [attachment({})],
        [attachment(FIELDS) | {"contentType": "text/plain"}],
        [attachment(FIELDS) | {"body": "*"}],
    ],
)
def test_absent_ambiguous_or_malformed_attachment_is_optional(
    tmp_path: Path, attachments: list[dict]
) -> None:
    case = project(tmp_path, attachments)
    assert case["passed"]
    assert case["observations"] == {}


@pytest.mark.parametrize(
    "scope",
    [
        {"filename": "unrelated.spec.ts"},
        {"title": EDIT + " twice"},
        {"group": "cutover"},
    ],
)
def test_other_cases_cannot_publish_this_observation(tmp_path: Path, scope: dict) -> None:
    assert project(tmp_path, [attachment(FIELDS)], **scope)["observations"] == {}


def test_files_must_resolve_within_the_owned_root(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(FIELDS))
    (root / "inside.json").write_text(json.dumps(FIELDS))
    att = {"name": TAG, "contentType": "application/json"}
    assert project(root, [att | {"path": "inside.json"}])["observations"] == {TAG: FIELDS}
    assert project(root, [att | {"path": "../outside.json"}])["observations"] == {}
