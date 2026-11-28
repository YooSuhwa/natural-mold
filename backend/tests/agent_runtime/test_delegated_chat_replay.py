"""Replay the same completed delegation from the 0.7.32 journal and the installed SDK.

The JSON Lines fixture holds the persist callback's output from _current_journal on
Deep Agents 0.7.32 / LangGraph 1.2.19. All messages and model responses are synthetic.
Each line is an entire persisted event, including upstream and synthesized tool events.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.models.conversation_run import ConversationRun
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.services import trace_storage
from tests.agent_runtime.langgraph_streaming_fixtures import sse_payload
from tests.agent_runtime.test_moldy_v3_runtime_state import FakeToolBindingChatModel
from tests.authz.unit_scope_fixture import unit_context
from tests.test_conversation_run_message_links import _seed_conversation

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)
_THREAD_ID = uuid.UUID("10000000-0000-4000-8000-000000000032")
_RUN_ID = uuid.UUID("40000000-0000-4000-8000-000000000032")
_ARCHIVE = Path(__file__).with_name("fixtures") / "delegated_chat_0_7_32.jsonl"
_ROOT_IDS = ["boss-task", "boss-final"]


async def _current_journal() -> list[dict[str, Any]]:
    supervisor = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                id="boss-task",
                content="위임합니다.",
                tool_calls=[
                    {
                        "id": "call-research",
                        "name": "task",
                        "args": {"description": "fixture only", "subagent_type": "researcher"},
                    }
                ],
            ),
            AIMessage(id="boss-final", content="조사를 마쳤습니다."),
        ]
    )
    child = FakeToolBindingChatModel(
        responses=[AIMessage(id="child-summary", content="대상은 세 곳입니다.")]
    )
    graph = create_deep_agent(
        model=supervisor,
        subagents=[
            {
                "name": "researcher",
                "description": "Collect fixture count.",
                "system_prompt": "Return fixture count.",
                "model": child,
            }
        ],
        checkpointer=InMemorySaver(),
    )
    records: list[dict[str, Any]] = []
    message_ids: list[str] = []

    async def capture(events: list[dict[str, Any]]) -> None:
        records.extend(events)

    async for _ in stream_agent_response_langgraph(
        graph,
        {"messages": [{"role": "user", "id": "user-request", "content": "세 곳을 확인해 줘."}]},
        {"configurable": {"thread_id": str(_THREAD_ID)}},
        run_id=str(_RUN_ID),
        persist_callback=capture,
        msg_id_sink=message_ids,
    ):
        pass
    assert message_ids == _ROOT_IDS
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["0.7.32", "installed"])
async def test_delegated_chat_keeps_root_message_links_and_child_identity_on_replay(
    db: AsyncSession, client: AsyncClient, source: str
) -> None:
    if source == "installed":
        records = await _current_journal()
    else:
        records = [json.loads(line) for line in _ARCHIVE.read_text(encoding="utf-8").splitlines()]
    assert {r["run_id"] for r in records} == {str(_RUN_ID)}
    assert {r["thread_id"] for r in records} == {str(_THREAD_ID)}
    agent, conversation = await _seed_conversation(db, conversation_id=_THREAD_ID)
    context = unit_context(agent.user_id)
    db.add(
        ConversationRun(
            id=_RUN_ID,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            tenant_id=agent.tenant_id,
            org_id=agent.org_id,
            source="chat",
            status="completed",
            is_active=False,
        )
    )
    await trace_storage.append_events(
        db, conversation_id=conversation.id, assistant_msg_id=str(_RUN_ID), events_chunk=records
    )
    await trace_storage.finalize_turn(
        db, assistant_msg_id=str(_RUN_ID), raw_msg_ids=_ROOT_IDS, conversation_id=conversation.id
    )
    await db.commit()

    links = await client.get(
        f"/api/conversations/{conversation.id}/run-message-links",
        params=[("message_id", mid) for mid in [*_ROOT_IDS, "child-summary", "call-research"]],
    )
    assert links.status_code == 200
    assert links.json() == [{"message_id": mid, "run_id": str(_RUN_ID)} for mid in _ROOT_IDS]
    events = await load_protocol_events(db, conversation.id, context=context)
    wire = [
        sse_payload(frame) async for frame in protocol_replay_generator(events, {}, after_id=None)
    ]
    (child_start,) = [
        e["params"]["data"]
        for e in wire
        if e["method"] == "lifecycle" and e["params"]["data"].get("graph_name") == "researcher"
    ]
    assert child_start["cause"] == {"type": "toolCall", "tool_call_id": "call-research"}
    child_namespace = child_start["namespace"]
    assert child_namespace and child_namespace[0].startswith("tools:")
    messages = [e for e in wire if e["method"] == "messages"]
    assert [
        (m["params"]["namespace"], m["params"]["data"]["id"], m["params"]["data"]["content"])
        for m in messages
    ] == [
        ([], "boss-task", "위임합니다."),
        (child_namespace, "child-summary", "대상은 세 곳입니다."),
        ([], "boss-final", "조사를 마쳤습니다."),
    ]
    assert wire[0]["params"]["data"] == {"event": "running"}
    assert wire[-1]["params"]["data"] == {"event": "completed"}
    tools = [e["params"]["data"] for e in wire if e["method"] == "tools"]
    assert {event["tool_call_id"] for event in tools} == {"call-research"}
    assert {event["event"] for event in tools} == {"tool-started", "tool-finished"}
    assert messages[0]["params"]["data"]["tool_calls"][0]["id"] == "call-research"

    reconnected = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["messages", "tools"]},
        headers={"Last-Event-ID": messages[1]["event_id"]},
    )
    assert reconnected.status_code == 200
    assert reconnected.headers["x-resume-mode"] == "replay"
    tail = [sse_payload(frame) for frame in reconnected.text.strip().split("\n\n")]
    expected_tail = wire[wire.index(messages[1]) + 1 :]
    assert tail == [e for e in expected_tail if e["method"] in {"messages", "tools"}]
