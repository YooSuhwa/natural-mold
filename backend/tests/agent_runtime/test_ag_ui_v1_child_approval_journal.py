"""A real nested checkpoint must resume the same public child and issuing tool."""

from __future__ import annotations

import pytest
from ag_ui.core import (
    BaseEvent,
    RunFinishedEvent,
    SubagentStartedEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.core.events import RunFinishedInterruptOutcome

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector
from tests.agent_runtime.ag_ui_v1_approval_fixture import THREAD, capture_approval_journals

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)


def _calls(frames: list[BaseEvent]) -> dict[str, ToolCallStartEvent]:
    starts = [frame for frame in frames if isinstance(frame, ToolCallStartEvent)]
    assert len(starts) == 2
    return {frame.tool_call_name: frame for frame in starts}


@pytest.mark.parametrize("reject", [False, True])
async def test_nested_checkpoint_resume_joins_both_tools_to_their_original_issuer(
    reject: bool,
) -> None:
    first, second = await capture_approval_journals(reject=reject, delegated=True)
    original = AgUiV1Projector(
        thread_id=THREAD, run_id="original", source_run_id=first[0]["run_id"]
    )
    waiting = [frame for row in first for frame in original.project(row)]
    continuation = AgUiV1Projector(
        thread_id=THREAD,
        run_id="continuation",
        source_run_id=second[0]["run_id"],
        tool_continuations=original.unresolved_tool_calls,
    )
    resumed = [frame for row in second for frame in continuation.project(row)]
    issued, continued = _calls(waiting), _calls(resumed)
    assert issued.keys() == continued.keys() == {"task", "create_note"}
    (original_child,) = [frame for frame in waiting if isinstance(frame, SubagentStartedEvent)]
    (resumed_child,) = [frame for frame in resumed if isinstance(frame, SubagentStartedEvent)]
    owner = original_child.subagent_run_id
    for child, calls in ((original_child, issued), (resumed_child, continued)):
        assert child.name == "writer" and child.subagent_run_id == owner
        assert child.parent_tool_call_id == calls["task"].tool_call_id
        assert calls["create_note"].subagent_run_id == owner
    terminal = waiting[-1]
    assert isinstance(terminal, RunFinishedEvent)
    assert isinstance(terminal.outcome, RunFinishedInterruptOutcome)
    (pending,) = terminal.outcome.interrupts
    assert pending.subagent_run_id == owner
    results = [frame for frame in resumed if isinstance(frame, ToolCallResultEvent)]
    assert len(results) == 2
    for name in issued:
        assert issued[name].tool_call_id == continued[name].tool_call_id
        assert issued[name].parent_message_id == continued[name].parent_message_id
        (result,) = [frame for frame in results if frame.tool_call_id == issued[name].tool_call_id]
        if name == "create_note":
            assert result.subagent_run_id == owner
            assert ("취소" if reject else "승인 문서를 만들었습니다.") in result.content
