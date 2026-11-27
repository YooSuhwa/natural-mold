"""Legacy task attribution is bound to the dispatch and the currently interrupted child."""

from typing import Any
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.pregel._task_status import read_task_statuses
from langgraph.types import Command

from app.agent_runtime.tools.ask_user import ask_user
from app.services.ag_ui_legacy_child_binding import (
    read_historical_child_bindings,
    read_legacy_child_bindings,
)
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY
from tests.agent_runtime.test_moldy_v3_runtime_state import FakeToolBindingChatModel


@pytest.mark.asyncio
async def test_parallel_delegates_are_bound_to_their_own_issued_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, run_id = uuid4(), uuid4()
    role = "agent_abcd0001"
    calls: list[Any] = [
        {"name": "ask_user", "id": "root-question", "args": {"question": "Root?"}},
        *[
            {"name": "task", "id": key, "args": {"subagent_type": role, "description": key}}
            for key in ("work-a", "work-b")
        ],
    ]
    parent = FakeToolBindingChatModel(responses=[AIMessage(content="", tool_calls=calls)])
    child = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "ask_user", "id": "question", "args": {"question": "Child?"}}],
            )
        ]
    )
    saver = InMemorySaver()
    graph = create_deep_agent(
        model=parent,
        tools=[ask_user],
        checkpointer=saver,
        subagents=[
            {
                "name": role,
                "description": "test",
                "system_prompt": "test",
                "model": child,
                "tools": [ask_user],
            }
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(uuid4()),
            "moldy_run_id": str(run_id),
            CHECKPOINT_TENANT_KEY: str(tenant_id),
        }
    }
    await graph.ainvoke({"messages": [{"role": "user", "content": "Ask three questions"}]}, config)
    saved = await saver.aget_tuple(config)
    assert saved is not None
    owners = {
        interrupt.id: task_id
        for task_id, status in read_task_statuses(saved.pending_writes or ()).items()
        for interrupt in status.pending_interrupts
    }
    assert len(owners) == 3

    async def read() -> dict[str, Any]:
        return await read_legacy_child_bindings(
            saver, saved, root_task_ids=owners, tenant_id=tenant_id, lineage={run_id}
        )

    bound = await read()
    assert len(bound) == 2
    assert {b.tool_call_id for b in bound.values()} == {"work-a", "work-b"}
    assert all(
        b.producer_run_id == run_id
        and b.runtime_name == role
        and b.checkpoint_ns == f"tools:{owners[ident]}"
        for ident, b in bound.items()
    )
    # A different child head, a foreign tenant, or a swapped tool call may never
    # authorize its sibling. The .32 child legitimately did not stamp a tool-call id.
    ident, one = next(iter(bound.items()))
    other = next(key for key in bound if key != ident)
    read_tuple = saver.aget_tuple
    for key, bad in (
        ("parents", {"": "previous-branch"}),
        (CHECKPOINT_TENANT_KEY, str(uuid4())),
        ("lc_agent_name", "agent_different"),
        ("lc_subagent_tool_call_id", "swapped"),
        ("moldy_run_id", str(uuid4())),
        ("lc_subagent_tool_call_id", None),
    ):

        async def changed_child(
            config: RunnableConfig, key: str = key, bad: Any = bad
        ) -> CheckpointTuple | None:
            row = await read_tuple(config)
            if not row or config.get("configurable", {}).get("checkpoint_ns") != one.checkpoint_ns:
                return row
            metadata = dict(row.metadata)
            if bad is None:
                metadata.pop(key, None)
            else:
                metadata[key] = bad
            return row._replace(metadata=metadata)

        monkeypatch.setattr(saver, "aget_tuple", changed_child)
        fresh = await read()
        assert fresh[other] == bound[other]
        assert fresh[ident] == (one if bad is None else None)


@pytest.mark.asyncio
async def test_consumed_child_is_read_from_its_issuing_run_after_resume() -> None:
    role, call_id = "agent_abcdef01", "historical-dispatch"
    tenant, origin, successor = uuid4(), uuid4(), uuid4()
    parent = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "id": call_id,
                        "args": {"subagent_type": role, "description": "test"},
                    }
                ],
            ),
            AIMessage(content="Completed"),
        ]
    )
    child = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "ask_user", "id": "ask", "args": {"question": "Which?"}}],
            ),
            AIMessage(content="Answered"),
        ]
    )
    saver = InMemorySaver()
    graph = create_deep_agent(
        model=parent,
        checkpointer=saver,
        subagents=[
            {
                "name": role,
                "description": "test",
                "system_prompt": "test",
                "model": child,
                "tools": [ask_user],
            }
        ],
    )
    thread = str(uuid4())
    config: RunnableConfig = {
        "configurable": {
            "thread_id": thread,
            "checkpoint_ns": "",
            CHECKPOINT_TENANT_KEY: str(tenant),
            "moldy_run_id": str(origin),
        }
    }
    await graph.ainvoke({"messages": [{"role": "user", "content": "test"}]}, config)
    issued = await saver.aget_tuple(config)
    assert issued is not None
    slot, task_id = next(
        (intr.id, task_id)
        for task_id, state in read_task_statuses(issued.pending_writes or ()).items()
        for intr in state.pending_interrupts
    )
    next_config: RunnableConfig = {
        "configurable": {
            **config.get("configurable", {}),
            "moldy_run_id": str(successor),
        }
    }
    await graph.ainvoke(
        Command(resume={slot: {"decisions": [{"type": "respond", "message": "Choice"}]}}),
        next_config,
    )
    latest = await saver.aget_tuple(config)
    assert latest is not None and latest.checkpoint["id"] != issued.checkpoint["id"]
    # Even the original checkpoint's writes now contain the answered task.
    retained = await saver.aget_tuple(issued.config)
    assert retained is not None
    assert all(
        not task.pending_interrupts
        for task in read_task_statuses(retained.pending_writes or ()).values()
    )
    result = await read_historical_child_bindings(
        saver, retained, tenant_id=tenant, lineage={origin}
    )
    assert result and len(result) == 1
    assert (
        result[0].runtime_name,
        result[0].tool_call_id,
        result[0].checkpoint_ns,
        result[0].producer_run_id,
    ) == (
        role,
        call_id,
        f"tools:{task_id}",
        origin,
    )
    assert (
        await read_historical_child_bindings(saver, retained, tenant_id=tenant, lineage={successor})
        is None
    )
    assert (
        await read_historical_child_bindings(saver, retained, tenant_id=uuid4(), lineage={origin})
        is None
    )
    assert (
        await read_historical_child_bindings(saver, latest, tenant_id=tenant, lineage={successor})
        == ()
    )
