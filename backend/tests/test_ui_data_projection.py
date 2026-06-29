from __future__ import annotations

import json

from app.agent_runtime.ui_data_projection import (
    DEMO_UI_DATA_TOOL_NAME,
    UI_DATA_TOOL_NAMES,
    ui_data_from_tool_result,
)


def test_operational_tool_names_are_empty() -> None:
    # Phase 1 regression-zero guarantee: no real tool projects ui_data.
    assert not UI_DATA_TOOL_NAMES


def test_projects_demo_note_from_recognized_demo_tool() -> None:
    result = json.dumps({"ui_type": "demo_note", "text": "hello"})

    payloads = ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id="call-1")

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

    payloads = ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id="c")

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
        {
            "ui_type": "chart",
            "chartType": "bar",
            "series": [{"label": "Mon", "value": 12}],
        }
    )

    payloads = ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id="c")

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

    payloads = ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id="c")

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


def test_unrecognized_tool_returns_empty() -> None:
    result = json.dumps({"ui_type": "demo_note", "text": "hello"})

    assert ui_data_from_tool_result("some_other_tool", result, tool_call_id="c") == []


def test_invalid_json_returns_empty() -> None:
    assert ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, "not json", tool_call_id=None) == []


def test_missing_ui_type_returns_empty() -> None:
    result = json.dumps({"text": "no ui_type here"})

    assert ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id=None) == []


def test_unknown_ui_type_fails_safe() -> None:
    # Unknown/unsupported type → ValidationError → drop (mirrors frontend fail-safe).
    result = json.dumps({"ui_type": "not_a_real_type", "foo": "bar"})

    assert ui_data_from_tool_result(DEMO_UI_DATA_TOOL_NAME, result, tool_call_id=None) == []


def test_non_dict_json_returns_empty() -> None:
    payloads = ui_data_from_tool_result(
        DEMO_UI_DATA_TOOL_NAME, json.dumps([1, 2]), tool_call_id=None
    )
    assert payloads == []
