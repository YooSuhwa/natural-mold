"""Deterministic scripted-model protocol for runtime Todo policy E2E."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

RUNTIME_TODO_POLICY_MARKER: Final = "E2E_RUNTIME_TODO_POLICY"
RUNTIME_TODO_POLICY_TOOL_CALL_ID_PREFIX: Final = "call_e2e_runtime_todo_policy_turn"
RUNTIME_TODO_POLICY_TOOL_CALL_ID: Final = f"{RUNTIME_TODO_POLICY_TOOL_CALL_ID_PREFIX}_1"
RUNTIME_TODO_POLICY_FINAL_CONTENT: Final = "E2E runtime Todo policy validation complete."
RUNTIME_TODO_POLICY_ITEMS: Final = (
    {"content": "Verify Todo policy snapshot", "status": "completed"},
    {"content": "Keep only the selected conversation plan", "status": "pending"},
)


def runtime_todo_policy_message(
    messages: Sequence[BaseMessage],
    human_text: str,
    *,
    bound_tool_names: Sequence[str],
) -> AIMessage | None:
    """Emit the Todo call only when the compiled agent exposes that capability."""
    if RUNTIME_TODO_POLICY_MARKER not in human_text:
        return None
    call_id = runtime_todo_policy_tool_call_id(_marker_turn_number(messages))
    if "write_todos" not in bound_tool_names or _has_tool_result(messages, call_id):
        return AIMessage(content=RUNTIME_TODO_POLICY_FINAL_CONTENT)
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": call_id,
                "name": "write_todos",
                "args": {"todos": [dict(item) for item in RUNTIME_TODO_POLICY_ITEMS]},
            }
        ],
    )


def runtime_todo_policy_tool_call_id(marker_turn_number: int) -> str:
    """Return the stable, per-marker-turn Todo tool-call id."""
    return f"{RUNTIME_TODO_POLICY_TOOL_CALL_ID_PREFIX}_{marker_turn_number}"


def _marker_turn_number(messages: Sequence[BaseMessage]) -> int:
    """Count Todo marker-bearing user turns so completed history cannot collide."""
    return sum(
        1
        for message in messages
        if isinstance(message, HumanMessage) and RUNTIME_TODO_POLICY_MARKER in str(message.content)
    )


def _has_tool_result(messages: Sequence[BaseMessage], tool_call_id: str) -> bool:
    return any(
        isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id
        for message in messages
    )


__all__ = [
    "RUNTIME_TODO_POLICY_FINAL_CONTENT",
    "RUNTIME_TODO_POLICY_MARKER",
    "RUNTIME_TODO_POLICY_TOOL_CALL_ID",
    "runtime_todo_policy_tool_call_id",
    "runtime_todo_policy_message",
]
