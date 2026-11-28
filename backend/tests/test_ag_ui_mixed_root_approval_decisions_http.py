"""Restore mixed root tool decisions through the authenticated snapshot route."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from ag_ui.core import ResumeEntry
from deepagents import create_deep_agent
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.approvals import service as approvals
from app.models.conversation_run import ConversationRun
from app.services import ag_ui_resume, chat_service, conversation_run_service, trace_storage
from app.services.conversation_resume_types import resume_decision_metadata
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.runtime_prototype import ScriptedModel
from tests.authz.unit_scope_fixture import unit_context
from tests.test_ag_ui_v1_read_recovery import _sign_in
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


async def _journal_graph(
    db: AsyncSession,
    graph: Any,
    run: ConversationRun,
    payload: dict[str, Any] | Command,
    status: Literal["interrupted", "completed"],
) -> Any:
    assert run.tenant_id is not None
    cfg = {"configurable": run_configurable(str(run.conversation_id), run.id, run.tenant_id)}
    await conversation_run_service.transition_run(db, run, "running")

    async def persist(batch: list[dict[str, Any]]) -> None:
        await trace_storage.append_events(
            db,
            conversation_id=run.conversation_id,
            assistant_msg_id=str(run.id),
            events_chunk=batch,
        )

    async for _ in stream_agent_response_langgraph(
        graph, payload, cfg, run_id=str(run.id), persist_callback=persist
    ):
        pass
    state = await graph.aget_state(cfg)
    assert bool(state.interrupts) == (status == "interrupted")
    await trace_storage.finalize_turn(db, assistant_msg_id=str(run.id), status="completed")
    await conversation_run_service.transition_run(
        db, run, status, interrupt_id=state.interrupts[0].id if state.interrupts else None
    )
    await db.commit()
    return state


@pytest.mark.asyncio
async def test_mixed_root_tool_decisions_restore_only_exact_source_issuances(
    raw_client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, conversation = await _seed_conversation(db)
    source = await _create_run(db, agent=agent, conversation=conversation)
    effects: list[str] = []

    @tool
    def record_note(title: str) -> str:
        """Record an approved note."""
        effects.append(title)
        return "Recorded."

    model = ScriptedModel(
        responses=[
            AIMessage(
                id="notes-assistant",
                content="",
                tool_calls=[
                    {"name": "record_note", "id": "first-note", "args": {"title": "first draft"}},
                    {"name": "record_note", "id": "next-note", "args": {"title": "next draft"}},
                ],
            ),
            AIMessage(id="notes-done", content="Finished."),
        ]
    )
    saver = InMemorySaver()
    graph = create_deep_agent(
        model=model,
        tools=[record_note],
        checkpointer=saver,
        interrupt_on={"record_note": {"allowed_decisions": ["approve", "edit", "reject"]}},
    )
    monkeypatch.setattr(checkpointer, "_checkpointer", saver)
    _sign_in(raw_client)
    url = f"/api/conversations/{conversation.id}/ag-ui/snapshot"
    state = await _journal_graph(
        db, graph, source, {"messages": [("user", "Review both notes.")]}, "interrupted"
    )
    (slot,) = state.interrupts
    assert len(slot.value["action_requests"]) == 2 and effects == []
    (request,) = await approvals.record_pending(
        db, source, [approvals.PendingInterrupt(slot.id, slot.value)], origin=approvals.RunOrigin()
    )
    await db.commit()
    waiting = await raw_client.get(url)
    assert waiting.status_code == 200, waiting.text
    assert [item["id"] for item in waiting.json()["interrupts"]] == [slot.id]
    [issuer] = [row for row in waiting.json()["messages"] if row.get("toolCalls")]
    calls = [call["id"] for call in issuer["toolCalls"]]
    assert len(calls) == len(set(calls)) == 2
    assert not {"first-note", "next-note"}.intersection(calls)

    choices = [
        {
            "type": "edit",
            "edited_action": {"name": "record_note", "args": {"title": "edited note"}},
        },
        {"type": "reject", "message": "private rejection note"},
    ]
    context = unit_context(source.user_id)
    await chat_service.require_executable_conversation(db, conversation.id, context)
    verified = await ag_ui_resume.verify_ag_ui_resume(
        db,
        conversation=conversation,
        context=context,
        entries=[
            ResumeEntry(interrupt_id=slot.id, status="resolved", payload={"decisions": choices})
        ],
    )
    resumed = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        context=context,
        source="resume",
        parent_run_id=source.id,
        interrupt_id=slot.id,
        input_preview=None,
        metadata=resume_decision_metadata(verified.resume, verified.pending_interrupts),
    )
    await ag_ui_resume.claim_verified_resume(
        db, verified=verified, actor_id=context.user_id, resumed_run_id=resumed.id
    )
    await db.commit()
    await _journal_graph(db, graph, resumed, Command(resume=verified.input_payload), "completed")
    await db.refresh(request)
    assert (request.resumed_run_id, request.decisions) == (resumed.id, ["edit", "reject"])
    assert effects == ["edited note"]

    expected = [
        {
            "messageId": issuer["id"],
            "toolCallId": call,
            "physicalRunId": str(source.id),
            "interruptId": slot.id,
            "actionIndex": index,
            "decision": decision,
        }
        for index, (call, decision) in enumerate(zip(calls, ["edit", "reject"], strict=True))
    ]
    for _tab in range(2):
        restored = await raw_client.get(url)
        assert restored.status_code == 200, restored.text
        assert restored.json()["interrupts"] == []
        decisions = restored.json()["approvalDecisions"]
        assert [{k: v for k, v in item.items() if v is not None} for item in decisions] == expected
    assert effects == ["edited note"]
