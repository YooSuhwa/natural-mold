from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TypedDict

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.models.message_event import MessageEvent
from app.services import conversation_run_message_links, conversation_run_service, trace_storage
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


class ControlledStreamFailure(RuntimeError):
    pass


class HistoricalMessage(TypedDict):
    id: str


class ValuesPayload(TypedDict):
    messages: list[HistoricalMessage]


class V3EventParams(TypedDict):
    namespace: list[str]
    data: BaseMessage | ValuesPayload


class V3Event(TypedDict):
    method: str
    params: V3EventParams
    seq: int


class PartialFailureProtocolAgent:
    def __init__(self, events: Sequence[V3Event]) -> None:
        self._events = tuple(events)

    async def astream_events(
        self,
        input_: None,
        *,
        config: dict[str, dict[str, str]],
        version: str,
    ) -> AsyncIterator[V3Event]:
        for event in self._events:
            yield event
        raise ControlledStreamFailure


@pytest.mark.asyncio
async def test_links_raw_runtime_message_key_to_its_canonical_historical_run(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_conversation(db)
    run = await _create_run(db, agent=agent, conversation=conversation)
    raw_message_id = "lc_run--final"
    canonical_message_id = parse_msg_id(raw_message_id, conversation.id, 0)
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            linked_message_ids=[str(canonical_message_id)],
            events=[],
        )
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/run-message-links",
        params=[
            ("message_id", raw_message_id),
            ("message_id", str(canonical_message_id)),
            ("message_id", raw_message_id),
        ],
    )

    assert response.status_code == 200
    assert response.json() == [
        {"message_id": raw_message_id, "run_id": str(run.id)},
        {"message_id": str(canonical_message_id), "run_id": str(run.id)},
    ]


@pytest.mark.asyncio
async def test_links_omit_reused_raw_message_key_attached_to_distinct_runs(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_conversation(db)
    first = await _create_run(db, agent=agent, conversation=conversation)
    await conversation_run_service.transition_run(db, first, "running")
    await conversation_run_service.transition_run(db, first, "completed")
    second = await _create_run(db, agent=agent, conversation=conversation)
    raw_message_id = "lc_run--final"
    canonical_message_id = parse_msg_id(raw_message_id, conversation.id, 0)
    db.add_all(
        [
            MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=str(run.id),
                linked_message_ids=[str(canonical_message_id)],
                events=[],
            )
            for run in (first, second)
        ]
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/run-message-links",
        params={"message_id": raw_message_id},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_v3_root_ai_message_id_survives_stream_finalize_and_owned_link_query(
    monkeypatch,
    db: AsyncSession,
) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    agent_model, conversation = await _seed_conversation(db)
    run = await _create_run(db, agent=agent_model, conversation=conversation)
    raw_message_id = f"lc_run--{run.id}"
    events: list[V3Event] = [
        {
            "method": "values",
            "params": {"namespace": [], "data": {"messages": [{"id": "history-ai"}]}},
            "seq": 1,
        },
        {
            "method": "messages",
            "params": {"namespace": [], "data": HumanMessage("current-user", id="user-id")},
            "seq": 2,
        },
        {
            "method": "messages",
            "params": {
                "namespace": ["child-agent"],
                "data": AIMessageChunk("child", id="child-ai"),
            },
            "seq": 3,
        },
        {
            "method": "messages",
            "params": {
                "namespace": [],
                "data": ToolMessage("tool", tool_call_id="call-1", id="tool-id"),
            },
            "seq": 4,
        },
        {
            "method": "messages",
            "params": {"namespace": [], "data": AIMessageChunk("done", id=raw_message_id)},
            "seq": 5,
        },
        {
            "method": "messages",
            "params": {"namespace": [], "data": AIMessageChunk("", id=raw_message_id)},
            "seq": 6,
        },
    ]
    protocol_agent = PartialFailureProtocolAgent(events)

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool,
        run_id: str | None,
    ) -> tuple[PartialFailureProtocolAgent, list[BaseMessage], dict[str, dict[str, str]]]:
        return protocol_agent, [], {"configurable": {"thread_id": str(conversation.id)}}

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    msg_id_sink: list[str] = []
    error_sink: list[StreamErrorRecord] = []
    cfg = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="help",
        tools_config=[],
        thread_id=str(conversation.id),
    )

    _ = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            cfg,
            [],
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            run_id=str(run.id),
        )
    ]
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            events=[],
        )
    )
    await db.flush()
    await trace_storage.finalize_turn(
        db,
        assistant_msg_id=str(run.id),
        raw_msg_ids=msg_id_sink,
        conversation_id=conversation.id,
        status="failed",
    )
    await db.commit()

    links = await conversation_run_message_links.list_run_message_links(
        db,
        conversation_run_message_links.RunMessageLinkQuery(
            conversation_id=conversation.id,
            user_id=agent_model.user_id,
            message_ids=[raw_message_id],
        ),
    )

    assert [link.model_dump(mode="json") for link in links] == [
        {"message_id": raw_message_id, "run_id": str(run.id)}
    ]
    assert msg_id_sink == [str(run.id), raw_message_id]
    assert len(error_sink) == 1
