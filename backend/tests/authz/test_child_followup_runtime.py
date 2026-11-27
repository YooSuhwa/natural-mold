"""Targeted follow-ups must retain each parallel task's own model history."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from deepagents.backends import StateBackend
from deepagents.middleware.subagents import create_sub_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import PrivateAttr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.agent_runtime import delegated_child_control, prepared_run_compile
from app.agent_runtime.delegated_child_control import ChildTaskLifecycle
from app.agent_runtime.identity import make_agent_runtime_name
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from app.agent_runtime.runtime_agent_builder import build_agent
from app.authz.org_context import OrgContext
from app.child_tasks import persistence as child_tasks_runtime
from app.child_tasks.errors import ChildTaskStopped
from app.config import settings
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_child_task import ChildInstructionCreate, ChildTaskResponse
from app.services import child_tasks_api
from app.services.conversation_run_state import transition_run
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.runtime_prototype import ScriptedModel
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.authz.test_prepared_runtime_installed_compiler import (
    RecordingAdmission,
    catalog_for,
    model,
    tool_messages,
)
from tests.sqlite_test_engine import TestSession

type SessionScope = Callable[[OrgContext], AbstractAsyncContextManager[AsyncSession]]

SOURCES = ("alpha", "beta")
FOLLOWUP = "Preserve only sources from a public institution."


class ResearchModel(ScriptedModel):
    """Issue a real graph tool call, then report what reached this child model."""

    _history: dict[str, list[list[BaseMessage]]] = PrivateAttr(default_factory=dict)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        source = next(
            str(msg.content)
            for msg in messages
            if isinstance(msg, HumanMessage) and msg.content in SOURCES
        )
        self._history.setdefault(source, []).append(list(messages))
        if any(isinstance(msg, ToolMessage) for msg in messages):
            response = AIMessage(content=f"report {source}")
        else:
            response = AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"source": source}, "id": f"lookup-{source}"}],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


class SlowResearch:
    def __init__(self) -> None:
        self.started = {source: asyncio.Event() for source in SOURCES}
        self.release = asyncio.Event()

    def tool(self) -> BaseTool:
        @tool("echo")
        async def lookup(source: str) -> str:
            """Collect the requested source."""
            self.started[source].set()
            await self.release.wait()
            return f"evidence {source}"

        return lookup

    @asynccontextmanager
    async def both_tasks_reading(
        self, graph: Any, config: dict[str, Any]
    ) -> AsyncIterator[asyncio.Task[Any]]:
        run = asyncio.create_task(
            graph.ainvoke({"messages": [("user", "Compare sources")]}, config)
        )
        starts = asyncio.gather(*(gate.wait() for gate in self.started.values()))
        try:
            async with asyncio.timeout(30):
                done, _ = await asyncio.wait((run, starts), return_when=asyncio.FIRST_COMPLETED)
            if run in done:
                result = await run
                pytest.fail(
                    f"Parent finished before both child lookups started: {tool_messages(result)}"
                )
            yield run
        finally:
            self.release.set()
            starts.cancel()
            run.cancel()
            await asyncio.gather(run, starts, return_exceptions=True)


def parallel_root(role: str, sources: tuple[str, ...] = SOURCES) -> ScriptedModel:
    calls = [
        {
            "name": "task",
            "args": {"description": source, "subagent_type": role},
            "id": f"delegate-{source}",
        }
        for source in sources
    ]
    return model(AIMessage(content="", tool_calls=calls), AIMessage(content="done"))


def assert_sibling_histories(child_model: ResearchModel) -> None:
    for source, expected_count in (("alpha", 1), ("beta", 0)):
        first, last = child_model._history[source]
        assert all(FOLLOWUP not in message.text for message in first)
        assert sum(FOLLOWUP in message.text for message in last) == expected_count
        assert [m.text for m in last if isinstance(m, ToolMessage)] == [f"evidence {source}"]


def child_graph(
    seeded: ChildCase,
    child: ResearchModel,
    lookup: BaseTool,
    saver: InMemorySaver,
    prepared: bool,
    *,
    root_model: ScriptedModel | None = None,
    require_approval: bool = False,
    child_middleware: tuple[AgentMiddleware[Any, Any, Any], ...] = (),
) -> Any:
    context = seeded.graph.context
    role = make_agent_runtime_name(seeded.child_id)
    lifecycle = ChildTaskLifecycle(context, seeded.graph.conversation_ids[0], seeded.child_id, role)
    spec = {
        "name": role,
        "system_prompt": "Research",
        "description": "Collect evidence",
        "model": child,
        "tools": [lookup],
        "_moldy_actor_id": seeded.child_id,
        "middleware": list(child_middleware),
    }
    if require_approval:
        spec["interrupt_on"] = {"echo": True}
    root = root_model or parallel_root(role)
    if not prepared:
        linked = {**spec, "middleware": [lifecycle.middleware(), *child_middleware]}
        return build_agent(
            root,
            [],
            "Coordinate researchers",
            checkpointer=saver,
            subagents=[linked],
            middleware=[
                delegated_child_control.DelegatedTaskDispatch(
                    delegated_child_control.task_lifecycles([linked])
                )
            ],
        )

    admission = RecordingAdmission()
    admission.lease_id = seeded.run_id
    common: dict[str, Any] = {
        "backend": StateBackend(),
        "projector": PreparedStateProjector(),
        "catalog": catalog_for([spec], root, []),
        "skills": None,
        "interrupt_on": None,
        "admission_factory": lambda: admission,
        "child_controls": {f"root/{role}": lifecycle},
    }
    runnables, _ = prepared_run_compile.compile_children(
        **common, compile_child=create_sub_agent, parent_middleware=[], guards=None
    )
    return prepared_run_compile.compile_root(
        **common,
        model=root,
        tools=[],
        system_prompt="Coordinate researchers",
        runnables=runnables,
        app_middleware=[],
        checkpointer=saver,
        memory=None,
    )


@pytest.fixture
def scoped_session(monkeypatch: pytest.MonkeyPatch) -> SessionScope:
    # The test suite's SQLite StaticPool has only one connection. Serialize its short
    # transactions; the graph still runs both children and parks both lookup calls together.
    sqlite_session_lock = asyncio.Lock()

    @asynccontextmanager
    async def scoped_session(ctx: OrgContext) -> AsyncIterator[AsyncSession]:
        async with sqlite_session_lock, database.tenant_session(ctx, maker=TestSession) as session:
            yield session

    monkeypatch.setattr(delegated_child_control, "tenant_session", scoped_session)
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    return scoped_session


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_instruction_reaches_only_one_parallel_invocation_of_same_child(
    child_case: ChildCase, scoped_session: SessionScope, prepared: bool
) -> None:
    seeded = child_case
    saver = InMemorySaver()
    child = ResearchModel(responses=[])
    slow = SlowResearch()
    graph = child_graph(seeded, child, slow.tool(), saver, prepared)
    conversation_id, context = seeded.graph.conversation_ids[0], seeded.graph.context
    config = {
        "configurable": run_configurable(str(conversation_id), seeded.run_id, context.tenant_id)
    }
    async with slow.both_tasks_reading(graph, config) as running:
        async with scoped_session(context) as session:
            started = await child_rows(session, conversation_id, context)
        assert {record.tool_call_id for record in started} == {"delegate-alpha", "delegate-beta"}
        target = next(record for record in started if record.tool_call_id == "delegate-alpha")
        request = ChildInstructionCreate(text=FOLLOWUP, client_request_id="deduplicated")
        for _ in range(2):
            async with scoped_session(context) as session:
                receipt = await child_tasks_api.instruct_child(
                    session, conversation_id, target.id, context, request
                )
            assert len(receipt.instructions) == 1
        slow.release.set()
        result = await asyncio.wait_for(running, 10)

    assert tool_messages(result) == {
        "delegate-alpha": "report alpha",
        "delegate-beta": "report beta",
    }
    assert_sibling_histories(child)
    assert "moldy_child_model_receipts" not in result
    assert "moldy_child_model_receipts" not in (await graph.aget_state(config)).values
    async with scoped_session(context) as session:
        finished = await child_rows(session, conversation_id, context)
    assert {task.id for task in finished} == {task.id for task in started}
    assert all("moldy_child_model_receipts" not in task.model_dump_json() for task in finished)
    assert {task.status for task in finished} == {"completed"}
    assert [(i.status, i.text) for task in finished for i in task.instructions] == [
        ("applied", FOLLOWUP)
    ]


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_cancel_one_child_during_tool_keeps_its_sibling_running(
    child_case: ChildCase, scoped_session: SessionScope, prepared: bool
) -> None:
    slow = SlowResearch()
    child = ResearchModel(responses=[])
    graph = child_graph(child_case, child, slow.tool(), InMemorySaver(), prepared)
    context = child_case.graph.context
    conversation_id = child_case.graph.conversation_ids[0]
    config = {
        "configurable": run_configurable(str(conversation_id), child_case.run_id, context.tenant_id)
    }
    async with slow.both_tasks_reading(graph, config) as running:
        async with scoped_session(context) as session:
            started = await child_rows(session, conversation_id, context)
        target = next(row for row in started if row.tool_call_id == "delegate-alpha")
        async with scoped_session(context) as session:
            await child_tasks_api.cancel_child(session, conversation_id, target.id, context)
        slow.release.set()
        result = await asyncio.wait_for(running, 30)
    responses = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
    assert responses["delegate-alpha"].status == "error"
    assert responses["delegate-beta"].text == "report beta"
    assert len(child._history["alpha"]) == 1 and len(child._history["beta"]) == 2


async def child_rows(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> list[ChildTaskResponse]:
    page = await child_tasks_api.list_child_tasks(db, conversation_id, context, limit=25, offset=0)
    assert page.next_offset is None
    return page.items


async def resume_case(db: AsyncSession, original: ChildCase) -> ChildCase:
    prior = await db.get(ConversationRun, original.run_id)
    assert prior is not None
    resumed = ConversationRun(
        conversation_id=prior.conversation_id,
        parent_run_id=prior.id,
        agent_id=prior.agent_id,
        user_id=prior.user_id,
        org_id=prior.org_id,
        tenant_id=prior.tenant_id,
        source="resume",
        run_source="CHAT",
        status="running",
        is_active=True,
        resource_owner_user_id=prior.resource_owner_user_id,
        on_behalf_of_user_id=prior.on_behalf_of_user_id,
        credential_subject_user_id=prior.credential_subject_user_id,
    )
    db.add(resumed)
    await db.flush()
    participants = list(
        await db.scalars(
            select(ConversationRunAgent).where(ConversationRunAgent.run_id == prior.id)
        )
    )
    db.add_all(
        [
            ConversationRunAgent(
                run_id=resumed.id,
                agent_id=actor.agent_id,
                agent_id_snapshot=actor.agent_id_snapshot,
                role=actor.role,
                path=actor.path,
                identity_mode_at_accept=actor.identity_mode_at_accept,
                resource_owner_user_id=actor.resource_owner_user_id,
                credential_subject_user_id=actor.credential_subject_user_id,
                on_behalf_of_user_id=actor.on_behalf_of_user_id,
            )
            for actor in participants
        ]
    )
    await db.commit()
    return replace(original, run_id=resumed.id)


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
@pytest.mark.parametrize("decision", ["authorized", "revoked", "cancelled", "feature_disabled"])
async def test_approval_holds_instruction_until_exact_child_resumes(
    child_case: ChildCase,
    scoped_session: SessionScope,
    prepared: bool,
    decision: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = child_case.graph.context
    conversation_id = child_case.graph.conversation_ids[0]
    slow, child, saver = SlowResearch(), ResearchModel(responses=[]), InMemorySaver()
    root = parallel_root(make_agent_runtime_name(child_case.child_id), ("alpha",))
    original_graph = child_graph(
        child_case, child, slow.tool(), saver, prepared, root_model=root, require_approval=True
    )

    def cfg(run: UUID) -> dict[str, Any]:
        return {"configurable": run_configurable(str(conversation_id), run, context.tenant_id)}

    result = await original_graph.ainvoke(
        {"messages": [("user", "research")]}, cfg(child_case.run_id)
    )
    assert len(result["__interrupt__"]) == 1
    assert not slow.started["alpha"].is_set() and len(child._history["alpha"]) == 1
    async with scoped_session(context) as db:
        old_run = await db.get(ConversationRun, child_case.run_id, with_for_update=True)
        assert old_run is not None
        await transition_run(db, old_run, "interrupted")
        await db.commit()
    async with scoped_session(context) as db:
        [suspended] = await child_rows(db, conversation_id, context)
    assert suspended.status == "awaiting_approval"
    async with scoped_session(context) as db:
        queued = await child_tasks_api.instruct_child(
            db,
            conversation_id,
            suspended.id,
            context,
            ChildInstructionCreate(text=FOLLOWUP, client_request_id="pending-approval"),
        )
    assert [r.status for r in queued.instructions] == ["pending"]
    if decision == "cancelled":
        async with scoped_session(context) as db:
            stopped = await child_tasks_api.cancel_child(db, conversation_id, suspended.id, context)
        assert stopped.status == "cancelled"
        assert [receipt.status for receipt in stopped.instructions] == ["cancelled"]
    if decision == "feature_disabled":
        monkeypatch.setattr(settings, "child_tasks_enabled", False)
    async with scoped_session(context) as db:
        resumed = await resume_case(db, child_case)
    resumed_graph = child_graph(
        resumed, child, slow.tool(), saver, prepared, root_model=root, require_approval=True
    )
    if decision == "revoked":
        async with scoped_session(context) as db:
            await db.execute(
                delete(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == child_case.graph.agent_id,
                    AgentSubAgentLink.sub_agent_id == child_case.child_id,
                )
            )
            await db.commit()
    if decision in {"revoked", "cancelled"}:
        result = await resumed_graph.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}), cfg(resumed.run_id)
        )
        assert not slow.started["alpha"].is_set()
        assert len(child._history["alpha"]) == 1
        assert [m.status for m in result["messages"] if isinstance(m, ToolMessage)] == ["error"]
        if decision == "revoked":
            # The handler was refused before it could rebind the suspended task.
            # Finish the *new* parent, then regrant access to read the durable state.
            async with scoped_session(context) as db:
                denied_resume = await db.get(ConversationRun, resumed.run_id, with_for_update=True)
                assert denied_resume is not None
                await transition_run(db, denied_resume, "completed")
                await db.commit()
                db.add(
                    AgentSubAgentLink(
                        parent_agent_id=child_case.graph.agent_id,
                        sub_agent_id=child_case.child_id,
                        delegated_by=context.user_id,
                    )
                )
                await db.commit()
            async with scoped_session(context) as db:
                [orphan] = await child_rows(db, conversation_id, context)
            assert orphan.id == suspended.id and orphan.status == "cancelled"
            assert [r.status for r in orphan.instructions] == ["cancelled"]
        return
    run_task = asyncio.create_task(
        resumed_graph.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}), cfg(resumed.run_id)
        )
    )
    gate = asyncio.create_task(slow.started["alpha"].wait())
    try:
        done, _ = await asyncio.wait(
            (run_task, gate), timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        if run_task in done:
            pytest.fail(f"Approved child ended before performing lookup: {await run_task}")
        assert gate in done
        async with scoped_session(context) as db:
            with pytest.raises(ChildTaskStopped):
                await child_tasks_runtime.check_child_task(
                    db, context=context, child_id=suspended.id, run_id=child_case.run_id
                )
        slow.release.set()
        result = await asyncio.wait_for(run_task, 30)
    finally:
        slow.release.set()
        gate.cancel()
        run_task.cancel()
        await asyncio.gather(gate, run_task, return_exceptions=True)
    assert tool_messages(result) == {"delegate-alpha": "report alpha"}
    assert sum(FOLLOWUP in m.text for m in child._history["alpha"][-1]) == 1
    async with scoped_session(context) as db:
        [finished] = await child_rows(db, conversation_id, context)
    assert finished.id == suspended.id and finished.status == "completed"
    assert [r.status for r in finished.instructions] == ["applied"]


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_parent_cancel_stops_all_attached_children(
    child_case: ChildCase, scoped_session: SessionScope, prepared: bool
) -> None:
    context = child_case.graph.context
    conversation_id = child_case.graph.conversation_ids[0]
    slow, child = SlowResearch(), ResearchModel(responses=[])
    graph = child_graph(child_case, child, slow.tool(), InMemorySaver(), prepared)
    cfg = {
        "configurable": run_configurable(str(conversation_id), child_case.run_id, context.tenant_id)
    }
    async with slow.both_tasks_reading(graph, cfg) as running:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    async with scoped_session(context) as db:
        attached = await child_rows(db, conversation_id, context)
    assert {r.tool_call_id for r in attached} == {"delegate-alpha", "delegate-beta"}
    assert {r.status for r in attached} == {"cancelled"}
    assert all(len(calls) == 1 for calls in child._history.values())
