from __future__ import annotations

import json
from typing import Any

from app.agent_runtime.ui_data_projection import (
    DEMO_UI_DATA_TOOL_NAME,
    UI_DATA_TOOL_NAMES,
    ui_data_from_tool_result,
)


def project_demo(result: str, *, tool_call_id: str | None) -> list[dict[str, Any]]:
    """Project a demo-tool result with the E2E demo recognition enabled."""

    return ui_data_from_tool_result(
        DEMO_UI_DATA_TOOL_NAME, result, tool_call_id=tool_call_id, demo_enabled=True
    )


def test_operational_tool_names_are_empty() -> None:
    # Phase 1 regression-zero guarantee: no real tool projects ui_data.
    assert not UI_DATA_TOOL_NAMES


def test_demo_tool_not_recognized_when_disabled() -> None:
    # Airtight regression-zero: without demo_enabled the demo tool name is NOT
    # recognized, so even a (hypothetical) production collision projects nothing.
    result = json.dumps({"ui_type": "demo_note", "text": "hello"})
    assert ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id="c") == []


def test_projects_demo_note_from_recognized_demo_tool() -> None:
    payloads = project_demo(
        json.dumps({"ui_type": "demo_note", "text": "hello"}), tool_call_id="call-1"
    )

    assert payloads == [
        {
            "schema_version": 1,
            "type": "demo_note",
            "message_id": None,
            "run_id": None,
            "tool_call_id": "call-1",
            "props": {"text": "hello"},
        }
    ]


def test_projects_data_table_from_recognized_demo_tool() -> None:
    result = json.dumps(
        {
            "ui_type": "data_table",
            "title": "t",
            "columns": [{"key": "a", "header": "A"}],
            "rows": [{"a": 1}],
        }
    )

    payloads = project_demo(result, tool_call_id="c")

    assert payloads == [
        {
            "schema_version": 1,
            "type": "data_table",
            "message_id": None,
            "run_id": None,
            "tool_call_id": "c",
            "props": {"title": "t", "columns": [{"key": "a", "header": "A"}], "rows": [{"a": 1}]},
        }
    ]


def test_projects_chart_from_recognized_demo_tool() -> None:
    result = json.dumps(
        {"ui_type": "chart", "chartType": "bar", "series": [{"label": "Mon", "value": 12}]}
    )

    payloads = project_demo(result, tool_call_id="c")

    assert payloads == [
        {
            "schema_version": 1,
            "type": "chart",
            "message_id": None,
            "run_id": None,
            "tool_call_id": "c",
            "props": {"chartType": "bar", "series": [{"label": "Mon", "value": 12}]},
        }
    ]


def test_projects_stats_from_recognized_demo_tool() -> None:
    result = json.dumps(
        {"ui_type": "stats", "items": [{"label": "총 요청", "value": 1240, "delta": 12}]}
    )

    payloads = project_demo(result, tool_call_id="c")

    assert payloads == [
        {
            "schema_version": 1,
            "type": "stats",
            "message_id": None,
            "run_id": None,
            "tool_call_id": "c",
            "props": {"items": [{"label": "총 요청", "value": 1240, "delta": 12}]},
        }
    ]


def test_projects_terminal_from_recognized_demo_tool() -> None:
    result = json.dumps(
        {"ui_type": "terminal", "command": "pytest -q", "exitCode": 0, "lines": ["3 passed"]}
    )

    payloads = project_demo(result, tool_call_id="c")

    assert payloads == [
        {
            "schema_version": 1,
            "type": "terminal",
            "message_id": None,
            "run_id": None,
            "tool_call_id": "c",
            "props": {"command": "pytest -q", "exitCode": 0, "lines": ["3 passed"]},
        }
    ]


def test_unrecognized_tool_returns_empty() -> None:
    result = json.dumps({"ui_type": "demo_note", "text": "hello"})

    assert (
        ui_data_from_tool_result("some_other_tool", result, tool_call_id="c", demo_enabled=True)
        == []
    )


def test_invalid_json_returns_empty() -> None:
    assert project_demo("not json", tool_call_id=None) == []


def test_missing_ui_type_returns_empty() -> None:
    assert project_demo(json.dumps({"text": "no ui_type here"}), tool_call_id=None) == []


def test_unknown_ui_type_fails_safe() -> None:
    # Unknown/unsupported type → ValidationError → drop (mirrors frontend fail-safe).
    assert (
        project_demo(json.dumps({"ui_type": "not_a_real_type", "foo": "bar"}), tool_call_id=None)
        == []
    )


def test_non_dict_json_returns_empty() -> None:
    assert project_demo(json.dumps([1, 2]), tool_call_id=None) == []


# ---------------------------------------------------------------------------
# W2-4 — transformer producers (execute_in_skill → terminal)
# ---------------------------------------------------------------------------


def test_execute_in_skill_projects_terminal_card() -> None:
    payloads = ui_data_from_tool_result(
        "execute_in_skill",
        "Row count: 42\n집계 완료",
        tool_call_id="call-skill-1",
    )

    assert payloads == [
        {
            "schema_version": 1,
            "type": "terminal",
            "message_id": None,
            "run_id": None,
            "tool_call_id": "call-skill-1",
            "props": {"lines": "Row count: 42\n집계 완료"},
        }
    ]


def test_execute_in_skill_strips_output_files_suffix() -> None:
    result = "stdout 본문\n\nOUTPUT_FILES: report.md, chart.png"
    payloads = ui_data_from_tool_result("execute_in_skill", result, tool_call_id="c")

    assert payloads[0]["props"] == {"lines": "stdout 본문"}


def test_execute_in_skill_empty_output_projects_nothing() -> None:
    assert ui_data_from_tool_result("execute_in_skill", "", tool_call_id="c") == []
    assert ui_data_from_tool_result("execute_in_skill", "   \n", tool_call_id="c") == []
    # OUTPUT_FILES만 있고 stdout이 비면 카드도 없다 (파일 칩은 pill이 담당).
    assert (
        ui_data_from_tool_result("execute_in_skill", "\n\nOUTPUT_FILES: a.png", tool_call_id="c")
        == []
    )


def test_execute_in_skill_truncates_huge_output() -> None:
    huge = "x" * 10_000
    payloads = ui_data_from_tool_result("execute_in_skill", huge, tool_call_id="c")

    lines = payloads[0]["props"]["lines"]
    assert len(lines) < 10_000
    assert lines.endswith("…[truncated]")


def test_transformer_active_without_demo_flag() -> None:
    # 실도구 transformer는 demo 게이트와 무관하게 항상 동작한다.
    payloads = ui_data_from_tool_result(
        "execute_in_skill", "ok", tool_call_id="c", demo_enabled=False
    )
    assert payloads[0]["type"] == "terminal"
