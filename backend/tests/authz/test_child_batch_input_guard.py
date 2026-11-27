"""A later harmless child instruction must not hide a blocked one from the shared guard."""

import asyncio
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from app.agent_runtime.identity import make_agent_runtime_name
from app.guardrails.middleware import Guard, GuardrailMiddleware
from app.schemas.conversation_child_task import ChildInstructionCreate
from app.services import child_tasks_api
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.test_child_followup_runtime import (
    ResearchModel,
    SessionScope,
    SlowResearch,
    child_graph,
    child_rows,
    parallel_root,
)
from tests.authz.test_child_followup_runtime import scoped_session as scoped_session
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.authz.test_prepared_runtime_installed_compiler import tool_messages
from tests.test_guardrail_steer_inputs import DIRECTIVE, INPUT_ONLY, PUBLIC


class WaitForChildInstructions(AgentMiddleware[Any, Any, Any]):
    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        self.ready.set()
        await self.release.wait()


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_guard_checks_each_pending_child_instruction_before_provider_dispatch(
    child_case: ChildCase, scoped_session: SessionScope, prepared: bool
) -> None:
    context = child_case.graph.context
    conversation_id = child_case.graph.conversation_ids[0]
    wait = WaitForChildInstructions()
    guard = Guard(INPUT_ONLY, context.tenant_id)
    child_model = ResearchModel(responses=[])
    reads = SlowResearch()
    reads.release.set()
    graph = child_graph(
        child_case,
        child_model,
        reads.tool(),
        InMemorySaver(),
        prepared,
        root_model=parallel_root(make_agent_runtime_name(child_case.child_id), ("alpha",)),
        child_middleware=(wait, GuardrailMiddleware(guard)),
    )
    config = {
        "configurable": run_configurable(str(conversation_id), child_case.run_id, context.tenant_id)
    }
    run = asyncio.create_task(graph.ainvoke({"messages": [("user", "research")]}, config))
    try:
        await asyncio.wait_for(wait.ready.wait(), 30)
        async with scoped_session(context) as session:
            [child] = await child_rows(session, conversation_id, context)
        for index, text in enumerate([DIRECTIVE, PUBLIC]):
            async with scoped_session(context) as session:
                await child_tasks_api.instruct_child(
                    session,
                    conversation_id,
                    child.id,
                    context,
                    ChildInstructionCreate(text=text, client_request_id=f"batch-{index}"),
                )
        wait.release.set()
        result = await asyncio.wait_for(run, 30)
    finally:
        wait.release.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)

    assert child_model._history == {}
    assert [decision.rule for decision in guard.decisions] == ["email-input"]
    assert "email-input" in tool_messages(result)["delegate-alpha"]
    assert "moldy_child_model_receipts" not in result
    async with scoped_session(context) as session:
        [finished] = await child_rows(session, conversation_id, context)
    assert [(row.text, row.status, row.applied_at) for row in finished.instructions] == [
        (DIRECTIVE, "review_required", None),
        (PUBLIC, "review_required", None),
    ]
