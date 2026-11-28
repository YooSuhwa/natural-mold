"""Offline delegated approval graph shared by the 0.7.32 producer and the upgraded reader."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from importlib.metadata import version
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


class _Model(FakeMessagesListChatModel):
    """Both processes select the next reply from persisted graph messages."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        reply = self.responses[int(any(isinstance(message, ToolMessage) for message in messages))]
        return ChatResult(generations=[ChatGeneration(message=reply)])


def build_graph(saver: BaseCheckpointSaver[Any], child_name: str, effects: list[str]) -> Any:
    @tool
    def record_review(subject: str) -> str:
        """Record a synthetic review for this regression."""
        effects.append(subject)
        return f"Reviewed {subject}"

    supervisor = _Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "delegate-review",
                        "name": "task",
                        "args": {"subagent_type": child_name, "description": "Review fixture."},
                    }
                ],
            ),
            AIMessage(content="Supervisor done."),
        ]
    )
    reviewer = _Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "review-fixture", "name": "record_review", "args": {"subject": "draft"}}
                ],
            ),
            AIMessage(content="Reviewer done."),
        ]
    )
    return create_deep_agent(
        model=supervisor,
        subagents=[
            {
                "name": child_name,
                "description": "Review a synthetic draft",
                "system_prompt": "Review the supplied draft.",
                "model": reviewer,
                "tools": [record_review],
                "interrupt_on": {"record_review": {"allowed_decisions": ["approve", "reject"]}},
            }
        ],
        checkpointer=saver,
    )


def config(thread_id: str | UUID, run_id: str | UUID, tenant_id: str | UUID) -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": str(thread_id),
            "moldy_run_id": str(run_id),
            "moldy_tenant_id": str(tenant_id),
        }
    }


async def seed(
    saver: BaseCheckpointSaver[Any],
    *,
    thread_id: str | UUID,
    run_id: str | UUID,
    tenant_id: str | UUID,
    child_name: str,
) -> dict[str, Any]:
    from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph

    cfg = config(thread_id, run_id, tenant_id)
    effects: list[str] = []
    graph = build_graph(saver, child_name, effects)
    journal: list[dict[str, Any]] = []

    async def retain(batch: list[dict[str, Any]]) -> None:
        journal.extend(batch)

    async for _ in stream_agent_response_langgraph(
        graph,
        {"messages": [{"role": "user", "content": "Please review this draft."}]},
        dict(cfg),
        run_id=str(run_id),
        persist_callback=retain,
    ):
        pass
    assert journal[-1]["method"] == "lifecycle"
    assert journal[-1]["data"]["event"] == "interrupted"
    state = await graph.aget_state(cfg)
    calls = [
        call
        for message in state.values["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == "task" and call["args"].get("subagent_type") == child_name
    ]
    assert len(calls) == 1
    saved = await saver.aget_tuple({"configurable": {"thread_id": str(thread_id)}})
    assert saved is not None and not effects
    live = [
        (task, interrupt)
        for task, channel, value in saved.pending_writes or ()
        if channel == "__interrupt__"
        for interrupt in value
    ]
    assert len(live) == 1, [event.get("method") for event in journal]
    task_id, interrupt = live[0]
    namespace = f"tools:{task_id}"
    child = await saver.aget_tuple(
        {"configurable": {"thread_id": str(thread_id), "checkpoint_ns": namespace}}
    )
    assert child is not None
    assert child.metadata.get("parents") == {"": saved.checkpoint["id"]}
    assert child.metadata.get("lc_agent_name") == child_name
    assert [
        slot
        for _, channel, values in child.pending_writes or ()
        if channel == "__interrupt__"
        for slot in values
    ] == [interrupt]
    return {
        "deepagents": version("deepagents"),
        "langgraph": version("langgraph"),
        "interruptId": interrupt.id,
        "rootCheckpointId": saved.checkpoint["id"],
        "taskId": task_id,
        "toolCallId": calls[0]["id"],
        "namespace": [namespace],
        "rawApproval": interrupt.value,
        "journal": journal,
        "childCheckpointId": child.checkpoint["id"],
        "childMetadata": dict(child.metadata),
    }


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread-id", type=UUID, required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--child-name", required=True)
    args = parser.parse_args()
    if version("deepagents") != "0.7.32" or version("langgraph") != "1.2.19":
        raise RuntimeError("Use the locked reference Deep Agents 0.7.32 / LangGraph 1.2.19")
    async with AsyncPostgresSaver.from_conn_string(os.environ["AG_UI_LEGACY_CHECKPOINT_DSN"]) as db:
        await db.setup()
        print(json.dumps(await seed(db, **vars(args))))


if __name__ == "__main__":
    asyncio.run(_main())
