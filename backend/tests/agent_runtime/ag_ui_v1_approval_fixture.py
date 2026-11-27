"""Capture both physical journals around a real, checkpointed Deep Agents approval."""

from __future__ import annotations

from typing import Any, cast

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.protocol_events import StoredProtocolEvent
from tests.authz.runtime_prototype import ScriptedModel

THREAD = "10000000-0000-4000-8000-000000000032"
FIRST_RUN = "40000000-0000-4000-8000-000000000041"
RESUMED_RUN = "40000000-0000-4000-8000-000000000042"


async def capture_approval_journals(
    *, reject: bool = False, delegated: bool = False
) -> tuple[list[StoredProtocolEvent], list[StoredProtocolEvent]]:
    executions: list[str] = []

    @tool
    def create_note(title: str) -> str:
        """Create the requested note once its author approves the title."""
        executions.append(title)
        return "승인 문서를 만들었습니다."

    model = ScriptedModel(
        responses=[
            AIMessage(
                id="approval-issuer",
                content="",
                tool_calls=[
                    {"name": "create_note", "id": "call_0", "args": {"title": "승인 문서"}}
                ],
            ),
            AIMessage(id="approval-done", content="처리가 끝났습니다."),
        ]
    )
    if delegated:
        supervisor = ScriptedModel(
            responses=[
                AIMessage(
                    id="delegating-assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": "approval-delegate",
                            "name": "task",
                            "args": {"subagent_type": "writer", "description": "문서 생성"},
                        }
                    ],
                ),
                AIMessage(id="supervisor-done", content="위임한 작업이 끝났습니다."),
            ]
        )
        graph = create_deep_agent(
            model=supervisor,
            subagents=[
                {
                    "name": "writer",
                    "description": "Create notes.",
                    "system_prompt": "Create a note.",
                    "model": model,
                    "tools": [create_note],
                    "interrupt_on": {"create_note": {"allowed_decisions": ["approve", "reject"]}},
                }
            ],
            checkpointer=InMemorySaver(),
        )
    else:
        graph = create_deep_agent(
            model=model,
            tools=[create_note],
            checkpointer=InMemorySaver(),
            interrupt_on={"create_note": {"allowed_decisions": ["approve", "reject"]}},
        )
    config = {"configurable": {"thread_id": THREAD}}

    async def capture(run_id: str, payload: Command | dict[str, Any]) -> list[StoredProtocolEvent]:
        journal: list[StoredProtocolEvent] = []

        async def persist(records: list[dict[str, Any]]) -> None:
            journal.extend(cast(list[StoredProtocolEvent], records))

        async for _ in stream_agent_response_langgraph(
            graph, payload, config, run_id=run_id, persist_callback=persist
        ):
            pass
        return journal

    first = await capture(
        FIRST_RUN, {"messages": [{"role": "user", "id": "approval-user", "content": "문서 생성"}]}
    )
    assert not executions
    # A child interrupt is also forwarded to the root namespace with the same ID.
    (pending,) = {
        event["data"]["interrupt_id"] for event in first if event["method"] == "input.requested"
    }
    decision = {"type": "reject", "message": "취소"} if reject else {"type": "approve"}
    resumed = await capture(RESUMED_RUN, Command(resume={pending: {"decisions": [decision]}}))
    assert executions == ([] if reject else ["승인 문서"])
    assert resumed[-1]["data"] == {"event": "completed"}
    return first, resumed
