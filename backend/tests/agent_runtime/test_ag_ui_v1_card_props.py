from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent_runtime.ag_ui_v1_card_props import (
    public_skill_draft,
    public_skill_validation,
    public_ui_data,
)
from app.agent_runtime.skill_validation_projection import skill_validation_event_from_tool_result
from app.agent_runtime.ui_data_projection import ui_data_from_tool_result
from app.schemas.skill_builder import SkillDraftFile
from app.skills.validator import validate_draft_package


@pytest.mark.parametrize(
    ("kind", "props", "expected"),
    [
        ("demo_note", {"text": "공개 메모", "raw": "private"}, {"text": "공개 메모"}),
        (
            "chart",
            {
                "chartType": "bar",
                "title": "Searches",
                "xLabel": "Month",
                "yLabel": "Count",
                "series": [{"label": "Nov", "value": 2, "input": "private"}],
            },
            {
                "chartType": "bar",
                "title": "Searches",
                "xLabel": "Month",
                "yLabel": "Count",
                "series": [{"label": "Nov", "value": 2}],
            },
        ),
        (
            "stats",
            {"items": [{"label": "Cost", "value": "12", "delta": -0.5, "unit": "$", "trace": {}}]},
            {"items": [{"label": "Cost", "value": "12", "delta": -0.5, "unit": "$"}]},
        ),
        (
            "terminal",
            {"command": "example", "lines": ["one", "two"], "exitCode": 0, "env": "private"},
            {"command": "example", "lines": ["one", "two"], "exitCode": 0},
        ),
    ],
)
def test_rendered_widget_props(kind: str, props: dict[str, Any], expected: dict[str, Any]) -> None:
    assert public_ui_data(
        {
            "type": kind,
            "props": props,
            "message_id": "msg-1",
            "run_id": "run-1",
            "tool_call_id": "call-1",
            "extra": "private",
        }
    ) == {
        "schema_version": 1,
        "type": kind,
        "props": expected,
        "message_id": "msg-1",
        "run_id": "run-1",
        "tool_call_id": "call-1",
    }


def test_skill_terminal_preserves_truncated_public_stdout_from_the_actual_producer() -> None:
    (event,) = ui_data_from_tool_result(
        "execute_in_skill", "x" * 7000 + "\n\nOUTPUT_FILES: internal", tool_call_id="call-1"
    )
    assert public_ui_data(event) == {
        "schema_version": 1,
        "type": "terminal",
        "tool_call_id": "call-1",
        "props": {"lines": "x" * 6000 + "\n…[truncated]"},
    }


def test_table_preserves_primitive_cells_only_under_rendered_columns() -> None:
    props = {
        "columns": [{"key": "row", "header": "표시", "meta": "private"}],
        "rows": [
            {"row": text, "hidden": "private"}
            for text in ["hello", 0, True, None, {}, ["private"], float("nan")]
        ],
        "title": "공개",
        "searchable": True,
        "raw": "private",
    }
    result = public_ui_data({"type": "data_table", "props": props})
    assert result is not None
    assert result["props"] == {
        "title": "공개",
        "searchable": True,
        "columns": [{"key": "row", "header": "표시"}],
        "rows": [{"row": "hello"}, {"row": 0}, {"row": True}, {"row": None}, {}, {}, {}],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": True, "type": "demo_note", "props": {"text": "test"}},
        {"schema_version": 2, "type": "demo_note", "props": {"text": "test"}},
        {"type": ["chart"]},
        {"type": "future", "props": {}},
        {"type": "terminal", "props": {"lines": "x" * 8193}},
        {"type": "terminal", "props": {"lines": ["x" * 8192, "x"]}},
        {"type": "terminal", "props": {"lines": ["one", {"raw": "private"}]}},
        {"type": "stats", "props": {"items": [{"label": "n", "value": True}]}},
        {"type": "stats", "props": {"items": [{"label": "n", "value": float("inf")}]}},
        {"type": "stats", "props": {"items": [{"label": "n", "value": 2**500}]}},
        {"type": "chart", "props": {"chartType": "other", "series": []}},
        {
            "type": "data_table",
            "props": {"columns": [{"key": "__proto__", "header": "p"}], "rows": []},
        },
        {"type": "demo_note", "message_id": "bad\nhandle", "props": {"text": "test"}},
    ],
)
def test_invalid_widgets_fail_closed(payload: dict[str, Any]) -> None:
    assert public_ui_data(payload) is None


def test_draft_has_only_relative_public_filenames_and_counts() -> None:
    files = [
        {"path": path, "content": "private", "size": 1}
        for path in (
            "SKILL.md",
            "문서/사용 설명서.md",
            "/private/file",
            "../private",
            r"C:\hidden\file",
            "https://server/file",
            "dir/../private",
            "~user/private",
            "dir//file",
            "a\x00b",
        )
    ]
    payload = {
        "session_id": "session-1",
        "slug": "기록 정리",
        "mode": "improve",
        "credential_requirement_count": 1,
        "changed_count": 44,
        "file_count": 27,
        "files": [*files, {"path": "bad", "size": False}],
        "credential_requirements": ["private"],
    }
    assert public_skill_draft(payload) == {
        "session_id": "session-1",
        "slug": "기록 정리",
        "mode": "improve",
        "credential_requirement_count": 1,
        "changed_count": 44,
        "file_count": 27,
        "files": [{"path": "SKILL.md", "size": 1}, {"path": "문서/사용 설명서.md", "size": 1}],
    }
    assert public_skill_draft({**payload, "file_count": -1}) is None
    assert public_skill_draft({**payload, "mode": "unknown"}) is None
    assert public_skill_draft({**payload, "session_id": {}}) is None


@pytest.mark.parametrize("tool_name", ["validate_skill", "finalize_skill"])
def test_real_validator_and_tool_bridge_cannot_quote_raw_yaml_in_either_issue_list(
    tool_name: str,
) -> None:
    files = [
        SkillDraftFile(
            path="SKILL.md", role="skill", content="---\nname: !RAW-CARD-SENTINEL x\n---\nbody"
        ),
        SkillDraftFile(
            path="agents/moldy.yaml", role="metadata", content="value: !RAW-CARD-SENTINEL x"
        ),
    ]
    raw = validate_draft_package(files=files)
    assert "RAW-CARD-SENTINEL" in json.dumps(raw)
    tool_result = raw if tool_name == "validate_skill" else {"validation_result": raw}
    event = skill_validation_event_from_tool_result(
        tool_name, json.dumps({**tool_result, "session_id": "session-1"})
    )
    assert event is not None
    card = public_skill_validation(event)
    assert card is not None
    assert card["session_id"] == "session-1"
    assert card["tool_name"] == tool_name
    result = card["validation_result"]
    assert "session_id" not in result
    assert result["valid"] is False
    assert result["error_count"] == raw["error_count"]
    assert {issue["code"] for issue in result["issues"]} == {
        "SKILL_MD_METADATA_INVALID",
        "MOLDY_METADATA_INVALID",
    }
    assert result["compatibility_result"]["targets"]["claude_code"]["status"] == "error"
    assert "RAW-CARD-SENTINEL" not in json.dumps(card)


def test_validation_allowlists_nested_issues_and_compatibility_targets() -> None:
    issue = {
        "code": "SECRET_DETECTED",
        "severity": "error",
        "message": "private",
        "finding_kind": "private",
        "path": "references/가이드.md",
    }
    public_issue = {
        "code": "SECRET_DETECTED",
        "severity": "error",
        "path": "references/가이드.md",
        "message": "Remove the potential secret from the package.",
    }
    payload = {
        "tool_name": "finalize_skill",
        "raw": "private",
        "validation_result": {
            "valid": False,
            "error_count": 9,
            "issues": [
                issue,
                {**issue, "code": "INVALID_PATH", "path": "/Users/private/file"},
                {**issue, "severity": "private"},
            ],
            "session_id": "private",
            "compatibility_result": {
                "targets": {
                    "unknown": {"status": "pass", "issues": [issue]},
                    "claude_code": {"status": "warning", "issues": [issue], "extra": "private"},
                },
                "status": "warning",
                "input": "private",
            },
        },
    }
    assert public_skill_validation(payload) == {
        "tool_name": "finalize_skill",
        "validation_result": {
            "valid": False,
            "error_count": 9,
            "issues": [
                public_issue,
                {
                    "code": "INVALID_PATH",
                    "severity": "error",
                    "message": "Use a package-relative path.",
                },
            ],
            "compatibility_result": {
                "targets": {"claude_code": {"issues": [public_issue], "status": "warning"}},
                "status": "warning",
            },
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": {}, "validation_result": {"valid": True, "issues": []}},
        {"tool_name": "other_tool", "validation_result": {"valid": True, "issues": []}},
        {"tool_name": "validate_skill", "validation_result": {"valid": 1, "issues": []}},
        {"tool_name": "validate_skill", "validation_result": {"valid": True, "issues": {}}},
        {
            "tool_name": "validate_skill",
            "validation_result": {"valid": False, "error_count": "1", "issues": []},
        },
    ],
)
def test_invalid_validation_cards_fail_closed(payload: dict[str, Any]) -> None:
    assert public_skill_validation(payload) is None
