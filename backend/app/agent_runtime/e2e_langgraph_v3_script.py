from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

LANGGRAPH_V3_MARKER: Final = "E2E_LANGGRAPH_V3"
LANGGRAPH_V3_SUBAGENT_MARKER: Final = "E2E_SUBAGENT"
LANGGRAPH_V3_SLOW_SUBAGENT_REQUEST: Final = "slow_subagent=true"
LANGGRAPH_V3_SLOW_SUBAGENT_MARKER: Final = "E2E_SUBAGENT_SLOW"
LANGGRAPH_V3_TODOS_TOOL_CALL_ID: Final = "call_e2e_langgraph_v3_todos"
LANGGRAPH_V3_SUBAGENT_TOOL_CALL_ID: Final = "call_e2e_langgraph_v3_subagent"
LANGGRAPH_V3_DOCX_TOOL_CALL_ID: Final = "call_e2e_langgraph_v3_docx"
LANGGRAPH_V3_SUBAGENT_RE: Final = re.compile(r"\bsubagent=(agent_[0-9a-f]{8})\b")
LANGGRAPH_V3_FINAL_MESSAGE: Final = (
    "E2E LangGraph v3 validation complete: todos, subagent, artifact, usage, and replay are ready."
)
LANGGRAPH_V3_SUBAGENT_PARTS: Final = ("E2E subagent ", "scoped ", "result ", "ready.")
LANGGRAPH_V3_SLOW_SUBAGENT_PARTS: Final = (
    "E2E subagent visual matrix: ",
    "planning context received; ",
    "todo state observed; ",
    "handoff accepted; ",
    "scoped tools indexed; ",
    "scratch context isolated; ",
    "delegate evidence streaming; ",
    "subagent delta still open; ",
    "intermediate summary visible; ",
    "artifact handoff prepared; ",
    "root agent waiting for delegated result; ",
    "ready.",
)
LANGGRAPH_V3_USAGE: Final = {
    "input_tokens": 120,
    "output_tokens": 45,
    "total_tokens": 165,
}
LANGGRAPH_V3_TODOS: Final = (
    {"content": "Collect LangGraph v3 runtime evidence", "status": "completed"},
    {"content": "Render delegated subagent progress", "status": "in_progress"},
    {"content": "Preview generated artifact and replay state", "status": "pending"},
)


def is_langgraph_v3_prompt(human_text: str) -> bool:
    return LANGGRAPH_V3_MARKER in human_text


def is_langgraph_v3_subagent_prompt(human_text: str) -> bool:
    return LANGGRAPH_V3_SUBAGENT_MARKER in human_text and LANGGRAPH_V3_MARKER not in human_text


def langgraph_v3_subagent_parts(human_text: str) -> tuple[str, ...]:
    if LANGGRAPH_V3_SLOW_SUBAGENT_MARKER in human_text:
        return LANGGRAPH_V3_SLOW_SUBAGENT_PARTS
    return LANGGRAPH_V3_SUBAGENT_PARTS


def langgraph_v3_subagent_response(human_text: str = "") -> AIMessage:
    return AIMessage(content="".join(langgraph_v3_subagent_parts(human_text)))


def langgraph_v3_message(
    messages: Sequence[BaseMessage],
    human_text: str,
    *,
    bound_tool_names: Sequence[str],
    docx_tool_args: Mapping[str, str],
) -> AIMessage:
    seen = _tool_message_ids(messages)
    if LANGGRAPH_V3_TODOS_TOOL_CALL_ID not in seen and _has_tool(bound_tool_names, "write_todos"):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": LANGGRAPH_V3_TODOS_TOOL_CALL_ID,
                    "name": "write_todos",
                    "args": {"todos": [dict(todo) for todo in LANGGRAPH_V3_TODOS]},
                }
            ],
        )

    if LANGGRAPH_V3_SUBAGENT_TOOL_CALL_ID not in seen and _has_tool(bound_tool_names, "task"):
        marker = (
            LANGGRAPH_V3_SLOW_SUBAGENT_MARKER
            if LANGGRAPH_V3_SLOW_SUBAGENT_REQUEST in human_text
            else LANGGRAPH_V3_SUBAGENT_MARKER
        )
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": LANGGRAPH_V3_SUBAGENT_TOOL_CALL_ID,
                    "name": "task",
                    "args": {
                        "subagent_type": _subagent_type(human_text),
                        "description": f"{marker} summarize scoped LangGraph v3 work.",
                    },
                }
            ],
        )

    if LANGGRAPH_V3_DOCX_TOOL_CALL_ID not in seen and _has_tool(
        bound_tool_names,
        "execute_in_skill",
    ):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": LANGGRAPH_V3_DOCX_TOOL_CALL_ID,
                    "name": "execute_in_skill",
                    "args": dict(docx_tool_args),
                }
            ],
        )

    return AIMessage(
        content=LANGGRAPH_V3_FINAL_MESSAGE,
        usage_metadata=dict(LANGGRAPH_V3_USAGE),
    )


def _has_tool(bound_tool_names: Sequence[str], name: str) -> bool:
    return name in bound_tool_names


def _subagent_type(human_text: str) -> str:
    match = LANGGRAPH_V3_SUBAGENT_RE.search(human_text)
    return match.group(1) if match else "agent_00000000"


def _tool_message_ids(messages: Sequence[BaseMessage]) -> set[str]:
    ids: set[str] = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            ids.add(message.tool_call_id)
    return ids


__all__ = [
    "LANGGRAPH_V3_MARKER",
    "LANGGRAPH_V3_SUBAGENT_PARTS",
    "LANGGRAPH_V3_SLOW_SUBAGENT_MARKER",
    "LANGGRAPH_V3_SLOW_SUBAGENT_PARTS",
    "is_langgraph_v3_prompt",
    "is_langgraph_v3_subagent_prompt",
    "langgraph_v3_message",
    "langgraph_v3_subagent_parts",
    "langgraph_v3_subagent_response",
]
