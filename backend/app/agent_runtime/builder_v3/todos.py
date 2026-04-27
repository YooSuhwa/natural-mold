"""진행 상황 카드 (Todo) 헬퍼.

각 phase 노드 진입/완료 시 호출하여 BuilderState.todos를 갱신하고,
ToolMessage로 emit하여 프론트엔드가 PhaseTimelineToolUI로 렌더링한다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.agent_runtime.builder_v3.constants import ToolNames
from app.agent_runtime.builder_v3.state import (
    PHASE_DEFINITIONS,
    BuilderState,
    PhaseTodo,
    initial_todos,
)

# 진행 상황 카드를 ToolMessage로 표시하기 위한 가짜 도구 이름.
PHASE_TIMELINE_TOOL = ToolNames.PHASE_TIMELINE


def update_phase_status(
    todos: list[PhaseTodo] | None, phase_id: int, status: str
) -> list[PhaseTodo]:
    """단일 phase의 status를 갱신한 새 todos 리스트를 반환한다 (불변)."""
    base = list(todos) if todos else initial_todos()
    new_todos: list[PhaseTodo] = []
    for t in base:
        if t["id"] == phase_id:
            new_todos.append({**t, "status": status})  # type: ignore[typeddict-item]
        else:
            new_todos.append(t)
    return new_todos


def mark_completed_through(todos: list[PhaseTodo] | None, phase_id: int) -> list[PhaseTodo]:
    """1..phase_id 까지를 completed, phase_id+1을 pending(기본값) 유지한 todos 반환."""
    base = list(todos) if todos else initial_todos()
    new_todos: list[PhaseTodo] = []
    for t in base:
        if t["id"] <= phase_id:
            new_todos.append({**t, "status": "completed"})  # type: ignore[typeddict-item]
        else:
            new_todos.append(t)
    return new_todos


def build_timeline_messages(
    state: BuilderState,
    *,
    intro_text: str | None = None,
) -> tuple[list[Any], list[PhaseTodo]]:
    """Tool call (assistant) + tool result (timeline) 메시지 쌍을 생성한다.

    assistant-ui는 tool_call_id로 tool 메시지를 매칭하므로, 두 메시지를 항상 함께 emit.

    Returns:
        (messages, updated_todos): messages는 add_messages reducer로 누적
    """
    todos = state.get("todos") or initial_todos()
    tool_call_id = str(uuid.uuid4())

    ai_msg = AIMessage(
        content=intro_text or "",
        tool_calls=[
            {
                "id": tool_call_id,
                "name": PHASE_TIMELINE_TOOL,
                "args": {"todos": [dict(t) for t in todos]},
            }
        ],
    )
    tool_msg = ToolMessage(
        content=json.dumps({"todos": [dict(t) for t in todos]}, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=PHASE_TIMELINE_TOOL,
    )
    return [ai_msg, tool_msg], todos


def get_phase_meta(phase_id: int) -> dict[str, Any]:
    for p in PHASE_DEFINITIONS:
        if p["id"] == phase_id:
            return dict(p)
    return {"id": phase_id, "name": f"Phase {phase_id}"}
