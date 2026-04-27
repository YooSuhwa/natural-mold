"""Builder v3 노드 공통 헬퍼.

- ToolMessage 페어(AIMessage + ToolMessage) 생성 — assistant-ui Tool UI용
- 진행 상황 카드 emit
- 텍스트 메시지 emit
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent_runtime.builder_v3.state import (
    BuilderState,
    PhaseTodo,
    get_phase_name,
    initial_todos,
)
from app.agent_runtime.builder_v3.todos import (
    PHASE_TIMELINE_TOOL,
    mark_completed_through,
    update_phase_status,
)


def make_tool_card(
    tool_name: str,
    args: dict[str, Any],
    *,
    intro_text: str = "",
) -> tuple[list[BaseMessage], str]:
    """assistant-ui가 렌더링하는 Tool UI 카드 (AIMessage + ToolMessage 페어)를 만든다.

    데이터 표시용 카드 (phase_timeline 등 결과만 보여주면 되는 경우).
    HiTL 입력 폼이 필요한 카드는 ``make_pending_tool_card`` 를 사용한다.

    Returns:
        (messages, tool_call_id)
    """
    tool_call_id = str(uuid.uuid4())
    ai_msg = AIMessage(
        content=intro_text,
        tool_calls=[{"id": tool_call_id, "name": tool_name, "args": args}],
    )
    tool_msg = ToolMessage(
        content=json.dumps(args, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=tool_name,
    )
    return [ai_msg, tool_msg], tool_call_id


def make_pending_tool_card(
    tool_name: str,
    args: dict[str, Any],
    *,
    intro_text: str = "",
) -> tuple[list[BaseMessage], str]:
    """HiTL 입력 폼용 — AIMessage(tool_calls)만 emit하고 ToolMessage는 생략한다.

    assistant-ui가 ``result === undefined`` 로 인식하여 입력 폼(요청 처리 대기)을
    렌더링하도록 하는 패턴. 사용자가 응답한 후 wait 노드에서
    ``close_pending_tool_card`` 로 ToolMessage를 추가하면 status가 complete로 전환되어
    카드가 더 이상 actionable하지 않게 된다.

    Returns:
        (messages, tool_call_id)
    """
    tool_call_id = str(uuid.uuid4())
    ai_msg = AIMessage(
        content=intro_text,
        tool_calls=[{"id": tool_call_id, "name": tool_name, "args": args}],
    )
    return [ai_msg], tool_call_id


def close_pending_tool_card(
    tool_call_id: str | None,
    tool_name: str,
    summary: str,
) -> list[BaseMessage]:
    """wait 노드 응답 처리 후 pending 카드를 close (status='complete'로 전환).

    ToolMessage(tool_call_id=...)를 emit하여 frontend의 result를 채운다.
    stale 카드가 다시 actionable해지지 않도록 한다.

    tool_call_id가 None이면 빈 리스트 반환 (no-op).
    """
    if not tool_call_id:
        return []
    return [
        ToolMessage(
            content=summary,
            tool_call_id=tool_call_id,
            name=tool_name,
        )
    ]


def parse_approval_response(response: Any) -> tuple[bool, str]:
    """approval interrupt 응답 → (approved, revision_text) 정규화.

    Phase 3/4/5 wait 노드가 공통으로 사용.
    """
    if isinstance(response, dict):
        approved = bool(response.get("approved"))
        revision = response.get("revision_message") or response.get("message") or ""
        return approved, revision
    if isinstance(response, str):
        return False, response
    return False, ""


def build_approval_result(
    *,
    state: BuilderState,
    approved: bool,
    revision: str,
    pending_tc_id: str | None,
    tool_name: str,
    phase_id: int,
    next_phase: int,
    completion_message: str,
    revision_default: str,
    clear_field: str,
) -> dict[str, Any]:
    """phase 3/4/5 approval 응답을 dict로 변환 (라우팅은 conditional_edges가).

    승인 시: completion 메시지 + ``current_phase`` 전진 + 카드 close.
    수정 시: ``last_revision_message`` 와 ``clear_field`` 클리어 + 카드 close.
    """
    if approved:
        close_msgs = close_pending_tool_card(pending_tc_id, tool_name, "승인됨")
        complete_msgs = build_phase_complete(
            phase_id, ensure_todos(state), completion_message
        )
        return {
            "messages": [*close_msgs, *complete_msgs],
            "current_phase": next_phase,
            "last_revision_message": None,
            "pending_tool_call_id": None,
        }

    revision_text = revision or revision_default
    close_msgs = close_pending_tool_card(
        pending_tc_id, tool_name, f"수정 요청: {revision_text}"
    )
    return {
        "messages": close_msgs,
        "last_revision_message": revision_text,
        clear_field: [] if clear_field in ("tools", "middlewares") else None,
        "pending_tool_call_id": None,
    }


def build_phase_intro(phase_id: int, todos: list[PhaseTodo] | None) -> list[BaseMessage]:
    """Phase 진입 시 표시할 메시지: 진행 상황 카드(in_progress) + 짧은 인사."""
    new_todos = update_phase_status(todos, phase_id, "in_progress")
    msgs, _ = make_tool_card(
        PHASE_TIMELINE_TOOL,
        {"todos": [dict(t) for t in new_todos]},
        intro_text=f"이제 Phase {phase_id}: {get_phase_name(phase_id)}을 진행하겠습니다.",
    )
    return msgs


def build_phase_complete(
    phase_id: int,
    todos: list[PhaseTodo] | None,
    summary_text: str,
) -> list[BaseMessage]:
    """Phase 완료 시: 완료 처리된 진행 상황 카드 + 요약 메시지."""
    new_todos = mark_completed_through(todos, phase_id)
    msgs, _ = make_tool_card(
        PHASE_TIMELINE_TOOL,
        {"todos": [dict(t) for t in new_todos]},
        intro_text=summary_text,
    )
    return msgs


def _extract_text_from_content(content: Any) -> str:
    """LangChain Message.content (str | list[block]) → plain string.

    Anthropic multi-block content (e.g. [{"type": "text", "text": "..."}, ...])
    를 raw stringify 대신 텍스트 블록만 추출한다.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content is not None else ""


def get_last_user_text(state: BuilderState) -> str:
    """state.messages에서 마지막 HumanMessage 텍스트를 가져온다."""
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage):
            return _extract_text_from_content(msg.content)
    return ""


def ensure_todos(state: BuilderState) -> list[PhaseTodo]:
    return state.get("todos") or initial_todos()


def updated_todos_after(phase_id: int, todos: list[PhaseTodo] | None) -> list[PhaseTodo]:
    """Phase X 완료 후의 todos: 1..X completed, X+1 in_progress 후보(pending 유지)."""
    return mark_completed_through(todos, phase_id)
