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


def test_usage_failure_preserves_scoped_network_categories_only(
    report: dict, tmp_path: Path
) -> None:
    spec = report["suites"][0]["specs"][0]
    spec.update(
        file="chat-langgraph-v3-regressions.spec.ts",
        title="resets token usage state when switching between conversations",
    )
    test = spec["tests"][0]
    attempt = test["results"][0]
    test["status"], attempt["status"], attempt["errors"] = "unexpected", "failed", [{}]
    marker = "private-request-info"
    read_failure = {
        "type": "moldy.api-request-failure.v1",
        "description": json.dumps(
            {
                "method": "GET",
                "pathname": f"/api/conversations/{marker}/ag-ui/snapshot",
                "errorText": "net::ERR_ABORTED",
                "extra": marker,
            }
        ),
    }
    test["annotations"] = [
        read_failure,
        {"type": "moldy.network-failure.v1", "description": "api_request_abort"},
    ]
    attempt["annotations"] = [
        read_failure,
        {
            "type": "moldy.response-failure.v1",
            "description": json.dumps(
                {"method": "GET", "pathname": f"/api/{marker}", "status": 503}
            ),
        },
        {"type": "moldy.network-failure.v1", "description": marker},
    ]
    args = {"group": "ordinary-regressions-ag_ui", "specs": [spec["file"]]}
    summary = playwright_report(report, tmp_path, **args)
    assert not summary["passed"]
    assert summary["cases"][0]["attempts"][0]["network"] == {
        "codes": ["api_request_abort"],
        "failures": [
            {"route": "ag_ui.snapshot", "method": "GET", "transport": "aborted"},
            {"route": "other", "method": "GET", "status": 503},
        ],
    }
    assert marker not in json.dumps(summary)
    spec["title"] = "another case"
    assert "network" not in playwright_report(report, tmp_path, **args)["cases"][0]["attempts"][0]


@pytest.mark.parametrize(
    "title",
    [
        "renders a demo_note data part in the bubble and keeps it after reload",
        "renders a chart data part as an SVG bar chart (Phase 2)",
    ],
)
def test_reloaded_data_card_keeps_navigation_provenance(
    report: dict, tmp_path: Path, title: str
) -> None:
    spec = report["suites"][0]["specs"][0]
    spec.update(file="chat-generative-ui.spec.ts", title=title)
    test = spec["tests"][0]
    attempt = test["results"][0]
    test["status"], attempt["status"], attempt["errors"] = "unexpected", "failed", [{}]
    attempt["annotations"] = [
        {
            "type": "moldy.api-request-failure.v1",
            "description": json.dumps(
                {
                    "method": "POST",
                    "pathname": "/api/conversations/private/ag-ui/runs",
                    "isMainFrame": True,
                    "startedBeforeCurrentMainFrameNavigation": True,
                    "errorText": "net::ERR_ABORTED",
                }
            ),
        }
    ]
    summary = playwright_report(
        report, tmp_path, group="ordinary-regressions-ag_ui", specs=[spec["file"]]
    )
    assert not summary["passed"]
    assert summary["cases"][0]["attempts"][0]["network"]["failures"] == [
        {
            "method": "POST",
            "route": "ag_ui.runs",
            "transport": "aborted",
            "isMainFrame": True,
            "startedBeforeCurrentMainFrameNavigation": True,
        }
    ]
    assert "private" not in json.dumps(summary)


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


def attach_waterfall(report: dict, value: dict) -> None:
    body = base64.b64encode(json.dumps(value).encode()).decode()
    report["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"] = [
        {"name": "perf-waterfall", "contentType": "application/json", "body": body}
    ]


@pytest.fixture
def timing() -> dict:
    epoch = 1_700_000_000_000
    return {
        "schemaVersion": 1,
        "expectedMode": "langgraph_v3",
        "mode": "langgraph_v3",
        "sampleId": "717fd7ad-a798-4be6-8ba5-c15d6b210d72",
        "conversationId": "1cfa5919-43f1-4998-85cc-0a62d891a710",
        "publicRunId": None,
        "physicalRunId": None,
        "runIds": [],
        "submittedAtMs": epoch,
        "firstFrameAtMs": epoch + 25,
        "lastFrameAtMs": epoch + 6000,
        "parserIssues": 0,
        "routes": [
            {
                "kind": "v3-command",
                "method": "POST",
                "requestAtMs": epoch,
                "headersAtMs": epoch + 4,
                "frameCount": 0,
            },
            {
                "kind": "v3-attach",
                "method": "POST",
                "requestAtMs": epoch - 200,
                "headersAtMs": epoch - 150,
                "frameCount": 14,
            },
        ],
        "ordinals": [
            {"ordinal": n, "networkAtMs": epoch + n * 900, "domAtMs": epoch + n * 900 + 12}
            for n in range(1, 7)
        ],
        "backendTiming": {
            "errors": [],
            "model": [{"ordinal": n, "sourceEpochMs": epoch + n * 850} for n in range(1, 7)],
            "journal": [{"ordinal": n, "commitEpochMs": epoch + 5500} for n in range(1, 7)],
        },
    }


def measured(report: dict, tmp_path: Path) -> dict:
    return playwright_report(
        report,
        tmp_path,
        group="performance-langgraph_v3",
        specs=["chat.spec.ts"],
        performance_timing=True,
    )


def test_batched_commit_and_v3_delivery_before_commit_are_valid(
    report: dict, timing: dict, tmp_path: Path
) -> None:
    attach_waterfall(report, timing)
    summary = measured(report, tmp_path)
    assert summary["passed"]
    sample = summary["cases"][0]["attempts"][0]["waterfall"]
    assert sample["complete"]
    assert not any(sample["missingOrdinals"].values())
    first = sample["ordinals"][0]
    assert first["networkAtMs"] < first["commitEpochMs"]
    assert first["commitEpochMs"] == sample["ordinals"][-1]["commitEpochMs"]
    assert sample["publicRunId"] is None


def test_partial_receipt_retains_observations_and_independent_ttft(
    report: dict, timing: dict, tmp_path: Path
) -> None:
    marker = "private-assertion-text"
    timing["backendTiming"]["errors"] = [marker]
    timing["backendTiming"]["model"][4]["sourceEpochMs"] = None
    timing["ordinals"][2]["networkAtMs"] = 0
    timing["routes"][0]["authorization"] = marker
    attach_waterfall(report, timing)
    summary = measured(report, tmp_path)
    assert not summary["passed"]
    assert marker not in json.dumps(summary)
    attempt = summary["cases"][0]["attempts"][0]
    assert attempt["perf"]["ttft_ms"] == 100
    assert attempt["waterfall"]["missingOrdinals"] == {
        "sourceEpochMs": [5],
        "commitEpochMs": [],
        "networkAtMs": [3],
        "domAtMs": [],
    }
    assert attempt["waterfall"]["ordinals"][2]["domAtMs"] == timing["ordinals"][2]["domAtMs"]


def test_mismatched_mode_and_duplicate_ordinal_are_unverified(
    report: dict, timing: dict, tmp_path: Path
) -> None:
    timing["mode"] = "unknown"
    timing["ordinals"].append(timing["ordinals"][0])
    attach_waterfall(report, timing)
    summary = measured(report, tmp_path)
    errors = summary["cases"][0]["attempts"][0]["waterfall"]["errors"]
    assert "transport_mode_unverified" in errors
    assert "duplicate_ordinal" in errors
    assert not summary["passed"]


def test_performance_attachment_mandatory_only_when_requested(report: dict, tmp_path: Path) -> None:
    assert not measured(report, tmp_path)["passed"]
    ordinary = playwright_report(
        report,
        tmp_path,
        group="ordinary-regressions-ag_ui",
        specs=["chat.spec.ts"],
        performance_timing=True,
    )
    assert ordinary["passed"]
    assert "waterfall" not in ordinary["cases"][0]["attempts"][0]
    unmeasured = playwright_report(
        report, tmp_path, group="performance-ag_ui", specs=["chat.spec.ts"]
    )
    assert unmeasured["passed"]
    assert "waterfall" not in unmeasured["cases"][0]["attempts"][0]


def test_spec_line_selector_matches_declaration_and_rejects_other_cases(
    report: dict, tmp_path: Path
) -> None:
    group = "ordinary-regressions-ag_ui"
    spec = report["suites"][0]["specs"][0]
    spec["line"] = 398
    report["suites"] = [
        {"specs": [], "suites": [{"file": "chat.spec.ts", "line": 310, "specs": [spec]}]}
    ]
    for selector in ("frontend/e2e/chat.spec.ts:398", "e2e/chat.spec.ts:310"):
        assert playwright_report(report, tmp_path, group=group, specs=[selector])["passed"]
    result = playwright_report(report, tmp_path, group=group, specs=["chat.spec.ts:505"])
    assert "missing_or_unexpected_spec_line" in result["errors"]
    suite = report["suites"][0]["suites"][0]
    suite["specs"].append({**spec, "title": "another selected case", "line": 505})
    report["stats"]["expected"] = 2
    result = playwright_report(report, tmp_path, group=group, specs=["chat.spec.ts:398"])
    assert "missing_or_unexpected_spec_line" in result["errors"]


def test_ag_ui_command_stream_carries_the_measured_response(
    report: dict, timing: dict, tmp_path: Path
) -> None:
    timing["expectedMode"] = timing["mode"] = "ag_ui"
    timing["publicRunId"] = "3d957dcc-aaff-41b9-a285-084c636ae108"
    route = timing["routes"].pop()
    route.update(kind="standard-command", method="POST")
    timing["routes"] = [route]
    attach_waterfall(report, timing)
    args = {"group": "performance-ag_ui", "specs": ["chat.spec.ts"], "performance_timing": True}
    assert playwright_report(report, tmp_path, **args)["passed"]
    route["frameCount"] = 0
    attach_waterfall(report, timing)
    result = playwright_report(report, tmp_path, **args)
    assert (
        "missing_stream_route_evidence" in result["cases"][0]["attempts"][0]["waterfall"]["errors"]
    )
