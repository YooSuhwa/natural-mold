"""Short-circuiting a child model must not acknowledge an instruction."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from app.agent_runtime.identity import make_agent_runtime_name
from app.schemas.conversation_child_task import ChildInstructionCreate
from app.services import child_tasks_api
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.test_child_followup_runtime import (
    FOLLOWUP,
    ResearchModel,
    SessionScope,
    SlowResearch,
    child_graph,
    child_rows,
    parallel_root,
)
from tests.authz.test_child_followup_runtime import (
    scoped_session as scoped_session,
)
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.authz.test_prepared_runtime_installed_compiler import tool_messages


class EarlyReturnGuard(AgentMiddleware[Any, Any, Any]):
    def __init__(self) -> None:
        self.registered, self.release = asyncio.Event(), asyncio.Event()
        self.seen: list[str] = []

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        self.registered.set()
        await self.release.wait()

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        self.seen.extend(m.text for m in request.messages if isinstance(m, HumanMessage))
        return ModelResponse(result=[AIMessage(content="Request blocked by policy")])


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_synthetic_guard_response_never_confirms_child_model_delivery(
    child_case: ChildCase, scoped_session: SessionScope, prepared: bool
) -> None:
    context, conversation_id = child_case.graph.context, child_case.graph.conversation_ids[0]
    child, guard = ResearchModel(responses=[]), EarlyReturnGuard()
    graph = child_graph(
        child_case,
        child,
        SlowResearch().tool(),
        InMemorySaver(),
        prepared,
        root_model=parallel_root(make_agent_runtime_name(child_case.child_id), ("alpha",)),
        child_middleware=(guard,),
    )
    cfg = {
        "configurable": run_configurable(str(conversation_id), child_case.run_id, context.tenant_id)
    }
    run = asyncio.create_task(graph.ainvoke({"messages": [("user", "research")]}, cfg))
    try:
        await asyncio.wait_for(guard.registered.wait(), 30)
        async with scoped_session(context) as db:
            [target] = await child_rows(db, conversation_id, context)
        async with scoped_session(context) as db:
            waiting = await child_tasks_api.instruct_child(
                db,
                conversation_id,
                target.id,
                context,
                ChildInstructionCreate(text=FOLLOWUP, client_request_id="before-guard"),
            )
        assert [r.status for r in waiting.instructions] == ["pending"]
        guard.release.set()
        result = await asyncio.wait_for(run, 30)
    finally:
        guard.release.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)

    assert guard.seen.count(FOLLOWUP) == 1 and child._history == {}
    assert tool_messages(result) == {"delegate-alpha": "Request blocked by policy"}
    assert "moldy_child_model_receipts" not in result
    async with scoped_session(context) as db:
        [finished] = await child_rows(db, conversation_id, context)
    assert [r.status for r in finished.instructions] == ["review_required"]
    assert finished.instructions[0].applied_at is None
    assert "moldy_child_model_receipts" not in finished.model_dump_json()
