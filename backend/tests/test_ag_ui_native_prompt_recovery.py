"""Recover the native tool or middleware prompt from the actual unresolved runtime slot."""

from __future__ import annotations

from typing import Any

import pytest
from ag_ui.core import RunFinishedEvent
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from langgraph.types import Command, Interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.tools.ask_user import ask_user
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, trace_storage
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.f07_support import Ledger, calls, guard, guarded, plain_tool, run_graph
from tests.authz.test_f07_connection_guard import _broken
from tests.authz.unit_scope_fixture import unit_context
from tests.test_ag_ui_v1_read_recovery import _sign_in, _sse
from tests.test_conversation_run_message_links import _create_run, _seed_conversation

pytestmark = pytest.mark.asyncio


async def _pause(
    db: AsyncSession,
    graph: Any,
    run: ConversationRun,
    payload: dict[str, Any] | Command,
) -> Interrupt:
    assert run.tenant_id is not None
    config = {"configurable": run_configurable(str(run.conversation_id), run.id, run.tenant_id)}
    await conversation_run_service.transition_run(db, run, "running")
    journal: list[dict[str, Any]] = []

    async def persist(batch: list[dict[str, Any]]) -> None:
        journal.extend(batch)

    async for _ in stream_agent_response_langgraph(
        graph, payload, config, run_id=str(run.id), persist_callback=persist
    ):
        pass
    [native] = (await graph.aget_state(config)).interrupts
    assert journal[-1]["data"] == {"event": "interrupted"}
    await trace_storage.append_events(
        db,
        conversation_id=run.conversation_id,
        assistant_msg_id=str(run.id),
        events_chunk=journal,
    )
    await trace_storage.finalize_turn(db, assistant_msg_id=str(run.id), status="completed")
    await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id=native.id)
    await db.commit()
    return native


async def _get_public_prompt(
    client: AsyncClient, run: ConversationRun, native: Interrupt
) -> dict[str, Any]:
    path = f"/api/conversations/{run.conversation_id}/ag-ui"
    params = {"runId": str(run.id)}
    streamed = await client.get(f"{path}/stream", params=params)
    restored = await client.get(f"{path}/snapshot", params=params)
    assert streamed.status_code == restored.status_code == 200
    frames = [record["event"] for record in _sse(streamed.text)]
    assert frames[0]["type"] == "RUN_STARTED"
    terminal = RunFinishedEvent.model_validate(frames[-1])
    assert terminal.outcome is not None and terminal.outcome.type == "interrupt"
    [live] = terminal.outcome.interrupts
    snapshot = restored.json()
    assert snapshot["interruptRunId"] == str(run.id)
    [saved] = snapshot["interrupts"]
    assert live.id == saved["id"] == native.id
    assert live.reason == saved["reason"]
    assert live.response_schema == saved["responseSchema"]
    assert live.metadata == saved["metadata"]
    return saved


@pytest.mark.parametrize("review_tool", [False, True], ids=["native-question", "tool-approval"])
async def test_native_question_and_same_named_middleware_approval_have_distinct_contracts(
    db: AsyncSession, raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, review_tool: bool
) -> None:
    agent, conversation = await _seed_conversation(db)
    run = await _create_run(db, agent=agent, conversation=conversation)
    args = {
        "mode": "option_list",
        "title": "과일 선택",
        "options": [{"id": "apple", "label": "사과"}, {"id": "orange", "label": "오렌지"}],
        "minSelections": 1,
        "maxSelections": 2,
    }
    model_call = AIMessage(
        content="", tool_calls=[{"id": "fruit", "name": "ask_user", "args": args}]
    )
    # Both configurations invoke the same tool, with identical options and decision type.
    # Only the middleware policy determines whether the actual tool has started.
    graph = run_graph(
        [model_call],
        [ask_user],
        [],
        {"ask_user": {"allowed_decisions": ["respond"]}} if review_tool else {},
    ).agent
    monkeypatch.setattr(checkpointer, "_checkpointer", graph.checkpointer)
    _sign_in(raw_client)
    native = await _pause(db, graph, run, {"messages": [("user", "과일을 고를게요.")]})
    saved = await _get_public_prompt(raw_client, run, native)
    data = saved["metadata"]["moldy"]
    assert saved["responseSchema"]
    if review_tool:
        assert native.value["action_requests"][0]["name"] == "ask_user"
        assert saved["reason"] == "moldy.tool_approval"
        assert data["action_requests"][0]["args"] == args
        assert data["review_configs"][0]["allowed_decisions"] == ["respond"]
    else:
        assert native.value == {"type": "ask_user", **args}
        assert saved["reason"] == "moldy.ask_user"
        assert data == native.value


async def test_auth_retry_restores_only_the_current_slot_and_decision_attempt(
    db: AsyncSession, raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, conversation = await _seed_conversation(db)
    run = await _create_run(db, agent=agent, conversation=conversation)
    ledger = Ledger()
    _broken(ledger, "account")
    graph = run_graph(
        [calls("lookup")],
        [plain_tool(ledger, "lookup")],
        [guard(ledger, guarded={"lookup": guarded("lookup", "account")})],
    ).agent
    monkeypatch.setattr(checkpointer, "_checkpointer", graph.checkpointer)
    _sign_in(raw_client)
    first = await _pause(db, graph, run, {"messages": [("user", "연결을 확인해 주세요.")]})
    first_prompt = await _get_public_prompt(raw_client, run, first)
    resumed = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        context=unit_context(run.user_id),
        source="resume",
        input_preview=None,
        parent_run_id=run.id,
        interrupt_id=first.id,
    )
    retry = {"decisions": [{"type": "respond", "message": "retry", "attempt": 1}]}
    second = await _pause(db, graph, resumed, Command(resume={first.id: retry}))
    second_prompt = await _get_public_prompt(raw_client, resumed, second)
    assert first.id != second.id and not ledger.ran
    for attempt, prompt in enumerate((first_prompt, second_prompt), start=1):
        assert prompt["reason"] == "moldy.auth_required"
        fields = prompt["metadata"]["moldy"]
        assert fields["auth_request_id"] == first.id and fields["attempt"] == attempt
        [item] = fields["items"]
        assert item["resource_name"] == "Slack" and item["tool_names"] == ["lookup"]
        [decision] = prompt["responseSchema"]["properties"]["decisions"]["prefixItems"]
        assert decision["properties"]["attempt"]["const"] == attempt
