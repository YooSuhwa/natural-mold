"""Parent and targeted child follow-ups share a run without crossing model inputs."""

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from deepagents.backends import StateBackend
from deepagents.middleware.subagents import create_sub_agent
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import PrivateAttr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import chat_steer_middleware as steer
from app.agent_runtime import prepared_run_compile
from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.agent_runtime.delegated_child_checkpoint import child_instruction_message_id
from app.agent_runtime.delegated_child_control import ChildTaskLifecycle, DelegatedTaskDispatch
from app.agent_runtime.identity import make_agent_runtime_name
from app.agent_runtime.prepared_run_write_capability import (
    PreparedRunWriteCapability,
    RunWriteBinding,
)
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from app.agent_runtime.runtime_agent_builder import build_agent
from app.database import SessionTenant
from app.schemas.conversation_child_task import ChildInstructionCreate
from app.schemas.conversation_graph_input import CHILD_RECEIPTS_CHANNEL, STEER_RECEIPTS_CHANNEL
from app.services import child_tasks_api, conversation_run_steers
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.runtime_prototype import ScriptedModel
from tests.authz.test_child_followup_runtime import (
    ResearchModel,
    SessionScope,
    SlowResearch,
    child_rows,
    parallel_root,
)
from tests.authz.test_child_followup_runtime import (
    scoped_session as scoped_session,
)
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.authz.test_prepared_runtime_installed_compiler import (
    RecordingAdmission,
    catalog_for,
    tool_messages,
)

PARENT_TEXT = "Produce a consolidated cost comparison."
CHILD_TEXT = "Filter this source to public institutions."


class RecordingRootModel(ScriptedModel):
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self._calls.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def compile_with_steer(
    case: ChildCase,
    root: RecordingRootModel,
    child: ResearchModel,
    research: SlowResearch,
    saver: InMemorySaver,
    prepared: bool,
    output_root: Path,
) -> Any:
    conversation_id, context = case.graph.conversation_ids[0], case.graph.context
    role = make_agent_runtime_name(case.child_id)
    lifecycle = ChildTaskLifecycle(context, conversation_id, case.child_id, role)
    spec = {
        "name": role,
        "system_prompt": "Collect evidence",
        "description": "Research",
        "tools": [research.tool()],
        "model": child,
        "_moldy_actor_id": case.child_id,
    }
    if not prepared:
        return build_agent(
            root,
            [],
            "Compare the research results",
            checkpointer=saver,
            subagents=[{**spec, "middleware": [lifecycle.middleware()]}],
            middleware=[
                DelegatedTaskDispatch({role: lifecycle}),
                steer.RunSteerMiddleware(conversation_run_steers.CONVERSATION_STEERS),
            ],
        )

    admission = RecordingAdmission()
    admission.lease_id = case.run_id
    capability = PreparedRunWriteCapability(
        RunWriteBinding(
            run_id=case.run_id,
            conversation_id=conversation_id,
            tenant_id=context.tenant_id,
            org_id=context.org_id,
            user_id=context.user_id,
            output_root=output_root,
        )
    )
    common: dict[str, Any] = {
        "backend": StateBackend(),
        "projector": PreparedStateProjector(),
        "catalog": catalog_for([spec], root, []),
        "skills": None,
        "interrupt_on": None,
        "admission_factory": lambda: admission,
        "child_controls": {f"root/{role}": lifecycle},
    }
    parent_middleware = [
        steer.RunSteerMiddleware(conversation_run_steers.CONVERSATION_STEERS, capability)
    ]
    runnables, _ = prepared_run_compile.compile_children(
        **common, compile_child=create_sub_agent, parent_middleware=parent_middleware, guards=None
    )
    return prepared_run_compile.compile_root(
        **common,
        model=root,
        tools=[],
        system_prompt="Compare the research results",
        runnables=runnables,
        app_middleware=parent_middleware,
        checkpointer=saver,
        memory=None,
    )


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_concurrent_parent_and_child_followups_have_distinct_delivery_proofs(
    child_case: ChildCase,
    scoped_session: SessionScope,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prepared: bool,
) -> None:
    context, conversation_id = child_case.graph.context, child_case.graph.conversation_ids[0]

    def root_session(tenant: SessionTenant) -> AbstractAsyncContextManager[AsyncSession]:
        assert tenant.tenant_id == context.tenant_id
        return scoped_session(context)

    monkeypatch.setattr(steer, "tenant_session", root_session)
    research, child, saver = SlowResearch(), ResearchModel(responses=[]), InMemorySaver()
    role = make_agent_runtime_name(child_case.child_id)
    root = RecordingRootModel(responses=parallel_root(role).responses)
    graph = compile_with_steer(child_case, root, child, research, saver, prepared, tmp_path)
    cfg = {
        "configurable": run_configurable(str(conversation_id), child_case.run_id, context.tenant_id)
    }
    # The two action namespaces can even use the same client-supplied retry ID.
    request_id = uuid4()
    async with research.both_tasks_reading(graph, cfg) as running:
        async with scoped_session(context) as db:
            active = await child_rows(db, conversation_id, context)
        target = next(r for r in active if r.tool_call_id == "delegate-alpha")
        for _ in range(2):
            async with scoped_session(context) as db:
                parent = await conversation_run_steers.admit_steer(
                    db,
                    conversation_id=conversation_id,
                    run_id=child_case.run_id,
                    context=context,
                    client_request_id=request_id,
                    text=PARENT_TEXT,
                )
                assert parent.status == "pending"
                await db.commit()
            async with scoped_session(context) as db:
                accepted = await child_tasks_api.instruct_child(
                    db,
                    conversation_id,
                    target.id,
                    context,
                    ChildInstructionCreate(text=CHILD_TEXT, client_request_id=str(request_id)),
                )
                assert [r.status for r in accepted.instructions] == ["pending"]
        research.release.set()
        result = await asyncio.wait_for(running, 30)

    parent_first, parent_final = root._calls
    assert all(m.text not in (CHILD_TEXT, PARENT_TEXT) for m in parent_first)
    assert all(CHILD_TEXT not in m.text for m in parent_final)
    assert [
        m.id for m in parent_final if isinstance(m, HumanMessage) and m.text == PARENT_TEXT
    ] == [steer.steer_message_id(parent.id)]
    assert {m.text for m in parent_final if isinstance(m, ToolMessage)} == {
        "report alpha",
        "report beta",
    }
    for source in ("alpha", "beta"):
        before, after = child._history[source]
        assert all(PARENT_TEXT not in m.text for m in [*before, *after])
        assert all(CHILD_TEXT not in m.text for m in before)
        seen = [m.id for m in after if isinstance(m, HumanMessage) and m.text == CHILD_TEXT]
        assert seen == (
            [child_instruction_message_id(accepted.instructions[0].id)] if source == "alpha" else []
        )
        assert [m.text for m in after if isinstance(m, ToolMessage)] == [f"evidence {source}"]
    assert tool_messages(result) == {
        "delegate-alpha": "report alpha",
        "delegate-beta": "report beta",
    }
    assert CHILD_RECEIPTS_CHANNEL not in result and STEER_RECEIPTS_CHANNEL not in result
    async with scoped_session(context) as db:
        [parent_row] = await conversation_run_steers.list_steers(
            db, conversation_id=conversation_id, run_id=child_case.run_id, context=context
        )
        assert (parent_row.id, parent_row.status) == (parent.id, "applied")
        assert parent_row.applied_at is not None
    assert await confirmed_steer_ids(
        conversation_id,
        child_case.run_id,
        [parent.id, accepted.instructions[0].id],
        saver=saver,
        tenant_id=context.tenant_id,
    ) == {parent.id}
    async with scoped_session(context) as db:
        rows = await child_rows(db, conversation_id, context)
    assert [(row.id, r.id, r.status) for row in rows for r in row.instructions] == [
        (target.id, accepted.instructions[0].id, "applied")
    ]
