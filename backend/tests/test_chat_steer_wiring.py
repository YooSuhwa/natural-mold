"""Both chat compilers deliver admitted instructions only to the selected root run."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import chat_steer_middleware, checkpointer, runtime_component_builder
from app.agent_runtime.chat_steer_middleware import (
    RunSteerMiddleware,
    RunSteerState,
    root_steer_middleware,
)
from app.agent_runtime.prepared_run_dispatch import handle_for, invoke_prepared
from app.agent_runtime.prepared_run_write_capability import RunWriteCapabilityDeniedError
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from app.agent_runtime.runtime_config import RuntimeComponents
from app.config import settings
from app.database import tenant_session
from app.schemas.conversation_graph_input import STEER_RECEIPTS_CHANNEL
from app.services.conversation_run_steers import admit_steer, list_steers
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession
from tests.test_executor import _cfg


@pytest.mark.parametrize(
    ("flag", "path", "trigger", "real_run", "expected"),
    [
        (True, "chat", False, True, True),
        (False, "chat", False, True, False),
        (True, "api", False, True, False),
        (True, "chat", True, True, False),
        (True, "chat", False, False, False),
    ],
)
async def test_ordinary_compiler_wires_only_interactive_runs(
    monkeypatch: pytest.MonkeyPatch,
    flag: bool,
    path: str,
    trigger: bool,
    real_run: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(settings, "chat_steer_enabled", flag)
    config = _cfg(thread_id=str(uuid4()), run_path=path)
    run_id = str(uuid4()) if real_run else None
    compiler = Mock()
    monkeypatch.setattr(runtime_component_builder, "build_agent", compiler)
    monkeypatch.setattr(checkpointer, "get_checkpointer", Mock())
    monkeypatch.setattr(
        runtime_component_builder,
        "_prepare_runtime_components",
        AsyncMock(
            return_value=RuntimeComponents(
                model_candidates=[],
                model=Mock(),
                tools=[],
                middleware=[],
                system_prompt="test",
                skills_sources=None,
                backend=None,
                memory_sources=None,
                permissions=[],
                interrupt_on=None,
            ),
        ),
    )
    _agent, _messages, runnable_config = await runtime_component_builder._prepare_agent(
        config,
        messages_history=[],
        run_id=run_id,
        is_trigger_mode=trigger,
    )
    stack = compiler.call_args.kwargs["middleware"] or []
    assert sum(isinstance(m, RunSteerMiddleware) for m in stack) == int(expected)
    if expected:
        assert runnable_config["configurable"]["moldy_run_id"] == run_id
        assert runnable_config["configurable"]["thread_id"] == config.thread_id
        assert runnable_config["configurable"]["moldy_tenant_id"] == str(
            config.org_context.tenant_id
        )


@pytest.mark.parametrize("key", ["moldy_run_id", "thread_id", "moldy_tenant_id"])
async def test_prepared_steer_gate_refuses_changed_identity_before_reading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    config = _cfg(thread_id=str(uuid4()), agent_id=str(uuid4()), user_id=str(uuid4()))
    run_id = str(uuid4())
    (middleware,) = root_steer_middleware(
        config, run_id=run_id, is_trigger_mode=False, prepared_output_root=tmp_path
    )
    saver = InMemorySaver()
    graph_config = {
        "thread_id": config.thread_id,
        "moldy_run_id": run_id,
        "moldy_tenant_id": str(config.org_context.tenant_id),
        "checkpoint_ns": "root:node",
        "__pregel_checkpointer": saver,
    }
    graph_config[key] = str(uuid4())
    monkeypatch.setattr(chat_steer_middleware, "get_config", lambda: {"configurable": graph_config})
    db_access = Mock(side_effect=AssertionError("unbound run touched storage"))
    monkeypatch.setattr(chat_steer_middleware, "tenant_session", db_access)
    state = RunSteerState(messages=[HumanMessage(content="test")])
    with pytest.raises(RunWriteCapabilityDeniedError):
        await middleware.abefore_model(state, Runtime())
    with pytest.raises(RunWriteCapabilityDeniedError):
        await middleware.aafter_model(state, Runtime())
    db_access.assert_not_called()


async def test_prepared_child_isolated_and_next_root_call_receives_steers_once(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def script(seed: akit.Seeded) -> dict[str, list[AIMessage]]:
        return {
            seed.root_model: [kit.task_call(seed.child_name, "research"), AIMessage("complete")],
            seed.child_model: [AIMessage("retained tool result")],
        }

    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    monkeypatch.setattr(
        chat_steer_middleware, "tenant_session", lambda ctx: tenant_session(ctx, maker=TestSession)
    )
    active = await akit.activate(db, tmp_path, monkeypatch, script)
    lease, seeded = active.lease, active.seeded
    assert "RunSteerMiddleware.before_model" in lease.runnable.nodes
    assert all(
        "RunSteerMiddleware.before_model" not in child.nodes
        for child in lease.child_runnables.values()
    )
    assert STEER_RECEIPTS_CHANNEL in PreparedStateProjector().private_state_keys
    child_started = asyncio.Event()
    release_child = asyncio.Event()
    requests: dict[str, list[list[BaseMessage]]] = defaultdict(list)
    generate = kit.GuardedScriptedModel._agenerate

    async def record(
        model: kit.GuardedScriptedModel, messages: list[BaseMessage], *args: Any, **kwargs: Any
    ) -> Any:
        tag = model._tag  # noqa: SLF001 - test provider boundary
        requests[tag].append(list(messages))
        if tag == seeded.child_model:
            child_started.set()
            await release_child.wait()
        return await generate(model, messages, *args, **kwargs)

    monkeypatch.setattr(kit.GuardedScriptedModel, "_agenerate", record)
    chat = {
        "conversation_id": lease.binding.conversation_id,
        "context": seeded.graph.context,
        "run_id": seeded.run_id,
    }
    instructions = ["국내 사례만", "최근 자료부터"]
    task = asyncio.create_task(invoke_prepared(handle_for(lease)))
    try:
        async with asyncio.timeout(12):
            await child_started.wait()
            async with TestSession() as tx:
                for text in instructions:
                    await admit_steer(tx, **chat, client_request_id=uuid4(), text=text)
                    await tx.commit()
    finally:
        release_child.set()
        result = await asyncio.wait_for(task, timeout=12)
    assert result["messages"][-1].content == "complete"
    assert len(requests[seeded.root_model]) == 2
    assert len(requests[seeded.child_model]) == 1
    initial, resumed = requests[seeded.root_model]
    assert not any(m.content in instructions for m in initial + requests[seeded.child_model][0])
    assert [
        m.content for m in resumed if isinstance(m, HumanMessage) and m.content in instructions
    ] == instructions
    tool_idx = next(
        i
        for i, m in enumerate(resumed)
        if isinstance(m, ToolMessage) and m.tool_call_id == "research"
    )
    assert resumed[tool_idx].content == "retained tool result"
    assert [m.content for m in resumed[tool_idx + 1 :]] == instructions
    async with TestSession() as tx:
        records = await list_steers(tx, **chat)
        assert [
            (row.sequence, row.status) for row in sorted(records, key=lambda r: r.sequence)
        ] == [(1, "applied"), (2, "applied")]
