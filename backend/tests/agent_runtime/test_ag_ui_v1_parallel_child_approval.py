"""A single native interrupt belongs to exactly one of two same-role siblings."""

from typing import Any, cast

import pytest
from ag_ui.core import (
    RunFinishedEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
    ToolCallResultEvent,
)
from ag_ui.core.events import RunFinishedInterruptOutcome, SubagentFinishedSuspendedOutcome
from deepagents import create_deep_agent
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.protocol_events import StoredProtocolEvent, protocol_interrupts_from_event
from tests.agent_runtime.ag_ui_v1_approval_fixture import FIRST_RUN, THREAD
from tests.authz.runtime_prototype import ScriptedModel

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)


class _Writer(ScriptedModel):
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        assignment = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        if assignment == "alpha":
            answer = AIMessage(
                id="alpha-ask",
                content="",
                tool_calls=[
                    {"name": "create_note", "id": "alpha-note", "args": {"title": "alpha"}}
                ],
            )
        else:
            assert assignment == "beta"
            answer = AIMessage(id="beta-answer", content="Beta completed.")
        return ChatResult(generations=[ChatGeneration(message=answer)])


async def test_completed_sibling_keeps_its_result_when_another_writer_awaits_approval() -> None:
    @tool
    def create_note(title: str) -> str:
        """Create a note after the user approves it."""
        pytest.fail(f"The pending tool ran before approval: {title}")

    supervisor = ScriptedModel(
        responses=[
            AIMessage(
                id="dispatch",
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "id": f"call-{name}",
                        "args": {"subagent_type": "writer", "description": name},
                    }
                    for name in ("alpha", "beta")
                ],
            )
        ]
    )
    graph = create_deep_agent(
        model=supervisor,
        subagents=[
            {
                "name": "writer",
                "description": "Write notes.",
                "system_prompt": "Write a note.",
                "model": _Writer(responses=[]),
                "tools": [create_note],
                "interrupt_on": {"create_note": {"allowed_decisions": ["approve", "reject"]}},
            }
        ],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": THREAD}}
    rows: list[StoredProtocolEvent] = []

    async def persist(records: list[dict[str, Any]]) -> None:
        rows.extend(cast(list[StoredProtocolEvent], records))

    async for _ in stream_agent_response_langgraph(
        graph,
        {"messages": [{"role": "user", "id": "request", "content": "Delegate both notes."}]},
        config,
        run_id=FIRST_RUN,
        persist_callback=persist,
    ):
        pass

    starts = {
        row["data"]["cause"]["tool_call_id"]: tuple(row["data"]["namespace"])
        for row in rows
        if row["method"] == "lifecycle" and row["data"]["event"] == "started"
    }
    alpha, beta = starts["call-alpha"], starts["call-beta"]
    assert alpha != beta
    assert {
        (tuple(row["data"]["namespace"]), row["data"]["event"])
        for row in rows
        if row["method"] == "lifecycle"
        and row["data"].get("namespace")
        and row["data"]["event"] != "started"
    } == {(alpha, "interrupted"), (beta, "completed")}

    state = await graph.aget_state(cast(RunnableConfig, config), subgraphs=True)
    (waiting,) = [task for task in state.tasks if task.interrupts]
    (pending,) = waiting.interrupts
    (finished,) = [task for task in state.tasks if task.result]
    assert isinstance(finished.result, dict)
    (result,) = finished.result["messages"]
    assert isinstance(result, ToolMessage) and result.tool_call_id == "call-beta"
    assert isinstance(result.artifact, dict) and result.artifact.get("checkpoint_ns") == beta[0]

    (authoritative,) = [
        slot
        for row in rows
        if row["method"] == "input.requested" and tuple(row["namespace"]) == alpha
        for slot in protocol_interrupts_from_event(row)
    ]
    assert authoritative["id"] == pending.id
    stream = AgUiV1Projector(thread_id=THREAD, run_id=FIRST_RUN, defer_terminal=True)
    wire = [frame for row in rows for frame in stream.project(row)]
    wire.extend(stream.finish("interrupted", interrupts=[authoritative]))
    opened = [f for f in wire if isinstance(f, SubagentStartedEvent)]
    assert len(opened) == 2 and all(f.name == "writer" for f in opened)
    outcomes = {f.subagent_run_id: f.outcome for f in wire if isinstance(f, SubagentFinishedEvent)}
    alpha_owner = stream.owner_for(alpha)
    beta_owner = stream.owner_for(beta)
    assert alpha_owner and beta_owner and set(outcomes) == {alpha_owner, beta_owner}
    [beta_start] = [child for child in opened if child.subagent_run_id == beta_owner]
    [public_result] = [frame for frame in wire if isinstance(frame, ToolCallResultEvent)]
    assert public_result.tool_call_id == beta_start.parent_tool_call_id
    assert public_result.content == result.content
    alpha_outcome = outcomes[alpha_owner]
    assert isinstance(alpha_outcome, SubagentFinishedSuspendedOutcome)
    assert alpha_outcome.interrupt_ids == [pending.id]
    beta_outcome = outcomes[beta_owner]
    assert beta_outcome is None or beta_outcome.type == "success"
    terminal = wire[-1]
    assert isinstance(terminal, RunFinishedEvent) and isinstance(
        terminal.outcome, RunFinishedInterruptOutcome
    )
    (slot,) = terminal.outcome.interrupts
    assert (slot.id, slot.subagent_run_id) == (pending.id, alpha_owner)
