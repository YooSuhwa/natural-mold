"""Goal notice observations retained in the ordinary browser acceptance report."""

# ruff: noqa: S101 -- pytest assertion statements.
import base64
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from evidence import playwright_report

GROUP = "ordinary-regressions-ag_ui"
SPEC = "chat-goal-steer-notice.spec.ts"
TITLE = "a goal edited during a tool call delivers one verified result after a fresh sign-in"
LIVE = "goal-live-child-proof"
FINAL = "goal-journey-proof"
PRIVATE = "private-attachment-text"


def public_id(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, label))


@pytest.fixture
def live() -> dict:
    return {
        "conversationId": public_id("conversation"),
        "homeOrgId": public_id("organization"),
        "goalId": public_id("goal"),
        "originalRun": public_id("original run"),
        "scopedGoalStatus": 200,
        "scopedRunStatus": 200,
        "scopedSnapshotStatus": 200,
        "childCount": 1,
        "boundTaskCalls": 1,
        "eligibleBriefs": 1,
        "pillCount": 1,
        "expectedBriefVisible": True,
        "expectedBriefOnParent": True,
        "steerButtonVisible": True,
        "runStatus": "running",
    }


@pytest.fixture
def final(live: dict) -> dict:
    return {
        "conversationId": live["conversationId"],
        "orgId": live["homeOrgId"],
        "goalId": live["goalId"],
        "originalRun": live["originalRun"],
        "steerId": public_id("steer"),
        "verifiedRun": public_id("verified run"),
        "versionId": public_id("version"),
        "artifactId": public_id("artifact"),
        "noticeId": public_id("notice"),
        "sha256": "af" * 32,
        "steerStatus": "applied",
        "goalVersion": 2,
        "automaticFollowups": 1,
        "completionNotices": 1,
        "foreignGoalNotices": 0,
        "freshLogin": True,
        "sameSingleNoticeAfterLogin": True,
        "anonymousStatusBeforeLogin": 401,
        "foreignVersionStatus": 404,
        "foreignDestinationStatus": 404,
    }


def attachment(name: str, payload: dict) -> dict:
    return {
        "name": name,
        "contentType": "application/json",
        "body": base64.b64encode(json.dumps(payload).encode()).decode(),
    }


def project(
    root: Path,
    attachments: list[dict],
    *,
    group: str = GROUP,
    spec: str = SPEC,
    title: str = TITLE,
    failed: bool = False,
) -> dict:
    attempt = {
        "retry": 0,
        "duration": 1234,
        "status": "failed" if failed else "passed",
        "errors": [{"message": PRIVATE}] if failed else [],
        "attachments": attachments,
    }
    test = {
        "expectedStatus": "passed",
        "status": "unexpected" if failed else "expected",
        "results": [attempt],
    }
    report = {
        "errors": [],
        "stats": {
            "expected": int(not failed),
            "unexpected": int(failed),
            "skipped": 0,
            "flaky": 0,
        },
        "suites": [{"specs": [{"file": spec, "title": title, "tests": [test]}]}],
    }
    summary = playwright_report(report, root, group=group, specs=[spec])
    assert PRIVATE not in json.dumps(summary)
    return summary


@pytest.mark.parametrize("group", [GROUP, "ordinary-regressions-langgraph_v3"])
def test_first_attempt_pass_exports_only_public_observations(
    tmp_path: Path, live: dict, final: dict, group: str
) -> None:
    attachments = [
        attachment(LIVE, {**live, "prompt": PRIVATE}),
        attachment(FINAL, {**final, "downloadUrl": PRIVATE}),
        attachment("screenshot-metadata", {"body": PRIVATE}),
    ]
    summary = project(tmp_path, attachments, group=group)
    assert summary["passed"]
    assert summary["counts"]["verified"] == 1
    assert summary["cases"][0]["observations"] == {LIVE: live, FINAL: final}


def test_failed_attempt_keeps_partial_scoped_observations(tmp_path: Path, live: dict) -> None:
    live.update(
        scopedGoalStatus=404,
        scopedRunStatus=0,
        scopedSnapshotStatus=503,
        runStatus=PRIVATE,
        childCount=0,
        expectedBriefVisible=False,
    )
    summary = project(tmp_path, [attachment(LIVE, live)], failed=True)
    assert not summary["passed"]
    assert summary["counts"]["verified"] == 0
    assert summary["cases"][0]["observations"] == {
        LIVE: {**live, "scopedRunStatus": None, "runStatus": None},
    }


@pytest.mark.parametrize(
    "context",
    [
        {"title": TITLE + " near match"},
        {"spec": "nested/" + SPEC},
        {"group": GROUP + "-other"},
    ],
)
def test_only_exact_goal_case_collects_proof(
    tmp_path: Path, live: dict, final: dict, context: dict
) -> None:
    summary = project(tmp_path, [attachment(LIVE, live), attachment(FINAL, final)], **context)
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == {}


@pytest.mark.parametrize("field", ["conversationId", "orgId", "goalId", "originalRun"])
def test_disagreed_scope_keeps_only_live_observation(
    tmp_path: Path, live: dict, final: dict, field: str
) -> None:
    final[field] = public_id("other scope")
    summary = project(tmp_path, [attachment(LIVE, live), attachment(FINAL, final)])
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == {LIVE: live}


def test_absent_attachments_do_not_change_pass_criteria(tmp_path: Path) -> None:
    summary = project(tmp_path, [])
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == {}


def test_final_proof_requires_independent_live_scope(tmp_path: Path, final: dict) -> None:
    summary = project(tmp_path, [attachment(FINAL, final)])
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == {}


@pytest.mark.parametrize("name", [LIVE, FINAL])
def test_duplicate_proof_name_is_ignored(
    tmp_path: Path, live: dict, final: dict, name: str
) -> None:
    attachments = [attachment(LIVE, live), attachment(FINAL, final)]
    attachments.append(attachment(name, {LIVE: live, FINAL: final}[name]))
    summary = project(tmp_path, attachments)
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == ({LIVE: live} if name == FINAL else {})


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        (LIVE, "goalId", PRIVATE),
        (LIVE, "boundTaskCalls", True),
        (LIVE, "scopedRunStatus", 99),
        (LIVE, "scopedGoalStatus", 600),
        (LIVE, "steerButtonVisible", "false"),
        (FINAL, "artifactId", PRIVATE),
        (FINAL, "sha256", PRIVATE),
        (FINAL, "sha256", "A" * 64),
        (FINAL, "foreignDestinationStatus", "404"),
        (FINAL, "steerStatus", PRIVATE),
    ],
)
def test_invalid_proof_fields_are_ignored(
    tmp_path: Path, live: dict, final: dict, name: str, field: str, value: object
) -> None:
    {LIVE: live, FINAL: final}[name][field] = value
    summary = project(tmp_path, [attachment(LIVE, live), attachment(FINAL, final)])
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == ({LIVE: live} if name == FINAL else {})


def test_malformed_attachment_does_not_hide_independent_observations(
    tmp_path: Path, live: dict
) -> None:
    invalid = {
        "name": FINAL,
        "contentType": "application/json",
        "body": "invalid-base64",
    }
    summary = project(tmp_path, [invalid, attachment(LIVE, live)])
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == {LIVE: live}


def test_on_disk_proofs_must_resolve_inside_owned_directory(
    tmp_path: Path, live: dict, final: dict
) -> None:
    owned = tmp_path / "playwright"
    owned.mkdir()
    outside = tmp_path / "other-journey.json"
    outside.write_text(json.dumps(final))
    (owned / "journey.json").symlink_to(outside)
    (owned / "live.json").write_text(json.dumps(live))
    summary = project(
        owned,
        [
            {"name": LIVE, "contentType": "application/json", "path": "live.json"},
            {"name": FINAL, "contentType": "application/json", "path": "journey.json"},
        ],
    )
    assert summary["passed"]
    assert summary["cases"][0]["observations"] == {LIVE: live}
    assert str(tmp_path) not in json.dumps(summary)
