"""Project tool results into ``moldy.ui_data`` events (memory_event_projection
pattern). chat-generative-ui-dev-plan §5.1.

Two producer paths:

1. **ui_type-carrying tools** — a tool whose name is in ``UI_DATA_TOOL_NAMES``
   (or the E2E demo tool when ``demo_enabled``) and whose JSON result carries
   ``ui_type`` is projected verbatim.
2. **Transformers (W2-4)** — tools in ``UI_DATA_TOOL_TRANSFORMERS`` whose raw
   results are NOT ui_data-shaped get an adapter that builds the payload. First
   real producer: ``execute_in_skill`` → ``terminal`` (stdout in a terminal
   card; the OUTPUT_FILES contract suffix is stripped — the skill pill renders
   file chips separately).

Contract: at most ONE payload per (tool_call, ui_type). The frontend dedup keys
on ``tc:<tool_call_id>:<type>`` (data-ui-events.ts) to collapse the same event
re-delivered across live/replay/re-synthesis, so a single tool result must not
yield two payloads of the same type under one tool_call_id.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Final

from pydantic import ValidationError

from app.schemas.ui_data import UIDataEvent

# Real tools whose results already carry ``ui_type``. Still empty — tools that
# need adaptation register a transformer below instead.
UI_DATA_TOOL_NAMES: Final[frozenset[str]] = frozenset()

# E2E-only demo tool (registered only when the scripted model is enabled — see
# ``tool_factory``/``runtime_component_builder``). Recognized here only when the
# caller passes ``demo_enabled`` so the demo proves the full pipeline without any
# operational exposure.
DEMO_UI_DATA_TOOL_NAME: Final = "e2e_ui_data_demo"

# Terminal payload cap — protects the SSE/persist path from megabyte stdouts.
_TERMINAL_MAX_CHARS: Final = 6000

# Appended by skill_executor after real stdout; the skill pill renders these as
# file chips, so the terminal card keeps pure stdout.
_OUTPUT_FILES_MARKER: Final = "\n\nOUTPUT_FILES:"


def _terminal_payload_from_execute_in_skill(result: str) -> list[dict[str, Any]]:
    text = result or ""
    marker_index = text.find(_OUTPUT_FILES_MARKER)
    if marker_index != -1:
        text = text[:marker_index]
    text = text.strip("\n")
    if not text.strip():
        return []
    if len(text) > _TERMINAL_MAX_CHARS:
        text = text[:_TERMINAL_MAX_CHARS] + "\n…[truncated]"
    return [{"ui_type": "terminal", "lines": text}]


# tool_name → adapter(result) -> payload dicts (each carrying ``ui_type``).
UI_DATA_TOOL_TRANSFORMERS: Final[dict[str, Callable[[str], list[dict[str, Any]]]]] = {
    "execute_in_skill": _terminal_payload_from_execute_in_skill,
}


def _ui_type_payloads(
    tool_name: str,
    result: str,
    *,
    demo_enabled: bool,
) -> list[dict[str, Any]]:
    recognized = tool_name in UI_DATA_TOOL_NAMES or (
        demo_enabled and tool_name == DEMO_UI_DATA_TOOL_NAME
    )
    if not recognized:
        return []
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(parsed, dict) or "ui_type" not in parsed:
        return []
    return [parsed]


def ui_data_from_tool_result(
    tool_name: str,
    result: str,
    *,
    tool_call_id: str | None,
    demo_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Project a tool result (JSON string or raw text) into ui_data payloads.

    ``demo_enabled`` (gated on ``e2e_scripted_model_enabled`` at the call site)
    additionally recognizes the E2E demo tool. Transformer tools
    (``UI_DATA_TOOL_TRANSFORMERS``) are always active — they adapt real tool
    output into a typed payload. Anything else projects nothing.
    """

    transformer = UI_DATA_TOOL_TRANSFORMERS.get(tool_name)
    if transformer is not None:
        raw_payloads = transformer(result)
    else:
        raw_payloads = _ui_type_payloads(tool_name, result, demo_enabled=demo_enabled)

    events: list[dict[str, Any]] = []
    for parsed in raw_payloads:
        props = {key: value for key, value in parsed.items() if key != "ui_type"}
        try:
            event = UIDataEvent(type=parsed["ui_type"], tool_call_id=tool_call_id, props=props)
        except ValidationError:
            # Unknown/unsupported ui_type → fail-safe (drop), mirroring the frontend.
            continue
        events.append(event.model_dump(mode="json"))
    return events
