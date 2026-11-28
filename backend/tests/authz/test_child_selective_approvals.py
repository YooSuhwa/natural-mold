"""Resume one of two same-role children across successive parent runs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent_runtime.identity import make_agent_runtime_name
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_child_task import ChildInstructionCreate, ChildTaskResponse
from app.services import child_tasks_api
from app.services.conversation_run_state import transition_run
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.test_child_followup_runtime import (
    ResearchModel,
    SessionScope,
    SlowResearch,
    child_graph,
    child_rows,
    parallel_root,
    resume_case,
)
from tests.authz.test_child_followup_runtime import scoped_session as scoped_session
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case

FOLLOWUPS = {"alpha": "Cite the registry.", "beta": "Compare the delivery dates."}


def _interrupts(state: dict[str, Any]) -> dict[str, str]:
    return {
        item.value["action_requests"][0]["args"]["source"]: item.id
        for item in state.get("__interrupt__", ())
    }


async def _pause(case: ChildCase, sessions: SessionScope) -> None:
    async with sessions(case.graph.context) as db:
        run = await db.get(ConversationRun, case.run_id, with_for_update=True)
        assert run is not None
        await transition_run(db, run, "interrupted")
        await db.commit()


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
@pytest.mark.parametrize("final_action", ["approve", "cancel-before-rebind"])
async def test_selective_child_approvals_preserve_sibling_receipts(
    child_case: ChildCase, scoped_session: SessionScope, prepared: bool, final_action: str
) -> None:
    context, conversation_id = child_case.graph.context, child_case.graph.conversation_ids[0]
    slow, child, saver = SlowResearch(), ResearchModel(responses=[]), InMemorySaver()
    root = parallel_root(make_agent_runtime_name(child_case.child_id))
    slow.release.set()

    async def advance(case: ChildCase, value: Any) -> dict[str, Any]:
        graph = child_graph(
            case, child, slow.tool(), saver, prepared, root_model=root, require_approval=True
        )
        config = run_configurable(str(conversation_id), case.run_id, context.tenant_id)
        return await graph.ainvoke(value, {"configurable": config})

    async def tasks() -> dict[str, ChildTaskResponse]:
        async with scoped_session(context) as db:
            rows = await child_rows(db, conversation_id, context)
        assert len(rows) == 2
        return {item.tool_call_id: item for item in rows}

    result = await advance(child_case, {"messages": [("user", "Compare the two sources.")]})
    interrupts = _interrupts(result)
    assert set(interrupts) == {"alpha", "beta"} and len(set(interrupts.values())) == 2
    assert not any(event.is_set() for event in slow.started.values())
    await _pause(child_case, scoped_session)
    initial = await tasks()
    assert {item.status for item in initial.values()} == {"awaiting_approval"}
    receipts: dict[str, UUID] = {}
    for source, text in FOLLOWUPS.items():
        async with scoped_session(context) as db:
            accepted = await child_tasks_api.instruct_child(
                db,
                conversation_id,
                initial[f"delegate-{source}"].id,
                context,
                ChildInstructionCreate(client_request_id="same-client-id", text=text),
            )
        [receipt] = accepted.instructions
        assert receipt.status == "pending"
        receipts[source] = receipt.id

    def approve(source: str) -> Command:
        return Command(resume={interrupts[source]: {"decisions": [{"type": "approve"}]}})

    async with scoped_session(context) as db:
        second = await resume_case(db, child_case)
    midway = await advance(second, approve("alpha"))
    assert _interrupts(midway) == {"beta": interrupts["beta"]}
    assert slow.started["alpha"].is_set() and not slow.started["beta"].is_set()
    middle = await tasks()
    assert {key: item.id for key, item in middle.items()} == {
        key: item.id for key, item in initial.items()
    }
    alpha, beta = middle["delegate-alpha"], middle["delegate-beta"]
    assert (alpha.status, alpha.instructions[0].status) == ("completed", "applied")
    assert (beta.status, beta.instructions[0].status) == ("awaiting_approval", "pending")
    await _pause(second, scoped_session)
    async with scoped_session(context) as db:
        third = await resume_case(db, second)
    assert len({third.run_id, second.run_id, child_case.run_id}) == 3

    if final_action == "cancel-before-rebind":
        async with scoped_session(context) as db:
            run = await db.get(ConversationRun, third.run_id, with_for_update=True)
            assert run is not None
            await transition_run(db, run, "canceling")
            await transition_run(db, run, "canceled")
            await db.commit()
    else:
        assert not _interrupts(await advance(third, approve("beta")))

    finished = await tasks()
    for source, text in FOLLOWUPS.items():
        row = finished[f"delegate-{source}"]
        assert row.id == initial[f"delegate-{source}"].id
        [receipt] = row.instructions
        assert (receipt.id, receipt.text) == (receipts[source], text)
        cancelled = final_action == "cancel-before-rebind" and source == "beta"
        assert (row.status, receipt.status) == (
            ("cancelled", "cancelled") if cancelled else ("completed", "applied")
        )
        assert slow.started[source].is_set() is not cancelled
        messages = child._history[source][-1]
        assert [message.text for message in messages if message.text in FOLLOWUPS.values()] == (
            [] if cancelled else [text]
        )
