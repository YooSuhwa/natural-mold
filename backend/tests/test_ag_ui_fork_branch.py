"""An edited sibling can pause, become the selected branch and receive its own answer."""

from __future__ import annotations

import uuid

import pytest
from ag_ui.core import ResumeEntry, RunAgentInput
from deepagents.graph import DeepAgentState
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from starlette.requests import Request

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.authz.worker_conversation_scope import WorkerConversationTarget
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_scope import ConversationBranchMutation
from app.services import conversation_run_service as runs
from app.services.ag_ui_accept_resume import accept_resume
from app.services.ag_ui_commands import find_or_reserve
from app.services.ag_ui_deferred_delivery import _prepare
from app.services.ag_ui_fork_checkpoints import load_fork_checkpoint
from app.services.ag_ui_pending_checkpoint import read_pending_checkpoint
from app.services.ag_ui_start import accept_start
from app.services.chat.conversation_mutations import set_active_branch
from app.services.conversation_run_transitions import _transition
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.execution_gateway_testkit import seed_graph
from tests.conftest import TestSession


def ask_fork(state: DeepAgentState) -> dict:
    request = state["messages"][-1].content
    if request == "fork":
        answer = interrupt({"type": "ask_user", "question": "Which?", "options": []})
        return {"messages": [AIMessage(content=answer["decisions"][0]["message"])]}
    return {"messages": [AIMessage(content="original answer")]}


@pytest.mark.parametrize("switch", [False, True])
async def test_interrupted_fork_moves_only_its_original_selection_and_can_resume(
    monkeypatch: pytest.MonkeyPatch, switch: bool
) -> None:
    async with TestSession() as db:
        seed = await seed_graph(db, conversations=1)
    ctx, cid = seed.context, seed.conversation_ids[0]
    user = CurrentUser(id=ctx.user_id, name="test", email="test@example.com")
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    saver = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    builder = StateGraph(DeepAgentState)
    builder.add_node("answer", ask_fork)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile(checkpointer=saver)
    config: RunnableConfig = {
        "configurable": run_configurable(str(cid), uuid.uuid4(), ctx.tenant_id)
    }
    await graph.ainvoke({"messages": [HumanMessage(content="original")]}, config)
    old = await graph.aget_state(config)
    selected = old.config["configurable"]["checkpoint_id"]
    target = [r async for r in saver.alist(config)][-1].config["configurable"]["checkpoint_id"]
    assert old.parent_config
    await graph.ainvoke({"messages": [HumanMessage(content="other branch")]}, old.parent_config)
    other = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
    async with TestSession() as db:
        await set_active_branch(db, ConversationBranchMutation(cid, ctx, selected))
        await db.commit()
    async with TestSession() as db:
        conv = await db.get(Conversation, cid)
        assert conv
        body = RunAgentInput.model_validate(
            {
                "threadId": str(cid),
                "runId": "fork",
                "tools": [],
                "context": [],
                "state": {},
                "messages": [{"role": "user", "id": "ui-fork", "content": "fork"}],
                "forwardedProps": {
                    "moldy": {"operation": "edit", "checkpointId": target, "expectedHead": selected}
                },
            }
        )
        admission = await find_or_reserve(db, ctx, cid, "fork", "1" * 64)
        receipt = await accept_start(
            db=db,
            user=user,
            context=ctx,
            conversation=conv,
            request=request,
            admission=admission,
            run_input=body,
        )
        assert receipt.run_id is not None
        physical = receipt.run_id
        await db.commit()
    async with TestSession() as db:
        pre = await _prepare(db, physical)
        assert pre
        native = pre.input_payload
        await runs.transition_run(db, pre.run, "running", worker_instance_id="fork-worker")
        await db.commit()
    conf = run_configurable(str(cid), physical, ctx.tenant_id)
    paused = await graph.ainvoke(native, {"configurable": {**conf, "checkpoint_id": target}})
    pending_id = paused["__interrupt__"][0].id
    active = await graph.aget_state(config)
    created = active.config["configurable"]["checkpoint_id"]
    assert created not in {other, selected, target}
    if switch:
        async with TestSession() as db:
            await set_active_branch(db, ConversationBranchMutation(cid, ctx, other))
            await db.commit()
    final, status = await _transition(
        physical,
        "interrupted",
        interrupt_id=pending_id,
        worker_instance_id="fork-worker",
        tenant=ctx,
        run_metrics=RunMetricsAccumulator(started_at=None),
        model_name="test",
        fork_target=WorkerConversationTarget(cid, ctx),
    )
    assert final and status == "interrupted"
    async with TestSession() as db:
        conv = await db.get(Conversation, cid)
        run = await db.get(ConversationRun, physical)
        assert conv and run
        assert conv.active_branch_checkpoint_id == (other if switch else created)
        pending = await read_pending_checkpoint(db, conversation=conv, context=ctx, parent_run=run)
        if switch:
            assert pending is None
            return
        assert pending and set(pending.native_kinds) == {pending_id}
        admission = await find_or_reserve(db, ctx, cid, "answer", "2" * 64)
        bound = await accept_resume(
            db=db,
            context=ctx,
            conversation=conv,
            user=user,
            request=request,
            admission=admission,
            entries=[
                ResumeEntry(
                    interrupt_id=pending_id,
                    status="resolved",
                    payload={"decisions": [{"type": "respond", "message": "confirmed"}]},
                )
            ],
        )
        assert bound.run_id
        resumed = await db.get(ConversationRun, bound.run_id)
        assert resumed and resumed.parent_run_id == physical
        await db.commit()
    async with TestSession() as db:
        subsequent = await _prepare(db, bound.run_id)
        assert subsequent and subsequent.cfg.checkpoint_id == created
        response = await graph.ainvoke(
            Command(resume=subsequent.input_payload),
            {"configurable": run_configurable(str(cid), bound.run_id, ctx.tenant_id)},
        )
        assert [m.content for m in response["messages"]] == ["fork", "confirmed"]


async def test_foreign_checkpoint_in_ancestor_chain_prevents_delta_replay() -> None:
    from tests.test_ag_ui_fork_checkpoints import conversation_graph

    saver = InMemorySaver()
    graph = conversation_graph(saver)
    tenant = uuid.uuid4()
    thread = str(uuid.uuid4())
    other = {"configurable": run_configurable(thread, uuid.uuid4(), uuid.uuid4())}
    await graph.ainvoke({"messages": [HumanMessage(content="foreign turn")]}, other)
    current: RunnableConfig = {"configurable": run_configurable(thread, uuid.uuid4(), tenant)}
    await graph.ainvoke({"messages": [HumanMessage(content="own turn")]}, current)
    head = (await graph.aget_state(current)).config["configurable"]["checkpoint_id"]
    assert (await saver.aget_tuple(current)).metadata["moldy_tenant_id"] == str(tenant)
    with pytest.raises(HTTPException) as exc:
        await load_fork_checkpoint(
            saver,
            thread_id=thread,
            tenant_id=tenant,
            selected=head,
            target=head,
            expected=head,
            operation="edit",
        )
    assert exc.value.status_code == 409
