"""Project tool results into ``moldy.ui_data`` events (memory_event_projection
pattern). chat-generative-ui-dev-plan §5.1.

A tool whose name is recognized AND whose JSON result carries ``ui_type`` is
projected into a :class:`UIDataEvent`. Operationally this is a **no-op**:
``UI_DATA_TOOL_NAMES`` is empty (Phase 2 registers real tools), and the only
recognized name is the E2E demo tool, which is registered solely when the
scripted model is enabled. Real conversations therefore emit zero ui_data
events — the regression-zero guarantee for Phase 1.
"""

from __future__ import annotations

import json
from typing import Any, Final

from pydantic import ValidationError

from app.schemas.ui_data import UIDataEvent

# Real tools that produce generative-UI payloads. Empty in Phase 1; Phase 2
# adds the tools whose results map to data_table/chart/stats/terminal.
UI_DATA_TOOL_NAMES: Final[frozenset[str]] = frozenset()

# E2E-only demo tool (registered only when the scripted model is enabled — see
# ``tool_factory``/``runtime_component_builder``). Recognized here so the demo
# proves the full pipeline without affecting operational traffic.
DEMO_UI_DATA_TOOL_NAME: Final = "e2e_ui_data_demo"

_RECOGNIZED_TOOL_NAMES: Final[frozenset[str]] = UI_DATA_TOOL_NAMES | {DEMO_UI_DATA_TOOL_NAME}


def ui_data_from_tool_result(
    tool_name: str,
    result: str,
    *,
    tool_call_id: str | None,
) -> list[dict[str, Any]]:
    """Project a tool result (JSON string) into ui_data payloads, or ``[]``.

    Contract: at most ONE payload per (tool_call, ui_type). The frontend dedup
    keys on ``tc:<tool_call_id>:<type>`` (data-ui-events.ts) to collapse the same
    event re-delivered across live/replay/re-synthesis, so a single tool result
    must not yield two payloads of the same type under one tool_call_id.
    """

    if tool_name not in _RECOGNIZED_TOOL_NAMES:
        return []
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(parsed, dict) or "ui_type" not in parsed:
        return []
    props = {key: value for key, value in parsed.items() if key != "ui_type"}
    try:
        event = UIDataEvent(type=parsed["ui_type"], tool_call_id=tool_call_id, props=props)
    except ValidationError:
        # Unknown/unsupported ui_type → fail-safe (drop), mirroring the frontend.
        return []
    return [event.model_dump(mode="json")]
