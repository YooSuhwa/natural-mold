from __future__ import annotations

import json
from typing import Any, Final

from app.agent_runtime import event_names

MEMORY_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"propose_memory", "save_user_memory", "save_agent_memory"}
)
MEMORY_EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        event_names.MEMORY_PROPOSED,
        event_names.MEMORY_SAVED,
        event_names.MEMORY_REJECTED,
        event_names.MEMORY_DELETED,
    }
)


def memory_event_from_tool_result(
    tool_name: str,
    result: str,
) -> tuple[str, dict[str, Any]] | None:
    if tool_name not in MEMORY_TOOL_NAMES:
        return None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    event = parsed.get("memory_event")
    if not isinstance(event, str) or event not in MEMORY_EVENT_NAMES:
        return None
    payload = {key: value for key, value in parsed.items() if key != "memory_event"}
    return event, payload
