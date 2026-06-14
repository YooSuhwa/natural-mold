from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_runtime.langgraph_pending_inputs import pending_input_requested_events


class _StateBackedAgent:
    def __init__(self, state: SimpleNamespace) -> None:
        self._state = state

    async def aget_state(self, _config: dict[str, Any]) -> SimpleNamespace:
        return self._state


def _approval_interrupt(interrupt_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=interrupt_id,
        value={
            "action_requests": [{"name": "execute_in_skill", "args": {"command": "make-docx"}}],
            "review_configs": [
                {"action_name": "execute_in_skill", "allowed_decisions": ["approve", "reject"]}
            ],
        },
    )


@pytest.mark.asyncio
async def test_pending_input_events_preserve_task_namespace_from_state_task_path() -> None:
    state = SimpleNamespace(
        interrupts=[],
        tasks=[
            SimpleNamespace(
                path=("tools:call-1", "__pregel_pull", "worker"),
                interrupts=[_approval_interrupt("intr-subgraph")],
            )
        ],
    )

    events = await pending_input_requested_events(
        _StateBackedAgent(state),
        {"configurable": {"thread_id": "thread-1"}},
        run_id="run-1",
        thread_id="thread-1",
        emitted=[],
    )

    assert len(events) == 1
    assert events[0]["method"] == "input.requested"
    assert events[0]["namespace"] == ["tools:call-1"]
    assert events[0]["data"]["namespace"] == ["tools:call-1"]
