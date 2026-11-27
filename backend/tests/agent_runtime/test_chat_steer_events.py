from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.authz.conversation_scope import require_conversation_agent_id
from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_run_steer import ConversationRunSteer
from app.models.message_event import MessageEvent
from app.routers.conversation_agent_protocol_replay import protocol_replay_generator
from app.services import conversation_run_service, conversation_run_steers, trace_storage
from app.services.conversation_protocol_event_projection import (
    RawEventProjectionRequest,
    stored_events_from_raw,
)
from app.services.conversation_protocol_replay import (
    LiveProtocolEventsRequest,
    load_live_protocol_events,
)
from app.services.conversation_run_steer_events import RunSteerReceiptFeed
from tests.agent_runtime.langgraph_streaming_fixtures import (
    FallbackAgent,
    ProtocolAgent,
    sse_payload,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.sqlite_test_engine import TestSession


@pytest.fixture
async def receipts(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[UUID, UUID, list[UUID]]:
    monkeypatch.setattr(database, "async_session", TestSession)
    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    context = unit_context(TEST_USER_ID)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=context,
        source="chat",
        input_preview="Research",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker")
    await db.commit()
    ids = []
    for text in ("Only domestic examples", "Include the original citations"):
        receipt = await conversation_run_steers.admit_steer(
            db,
            conversation_id=conversation_id,
            run_id=run.id,
            context=context,
            client_request_id=uuid4(),
            text=text,
        )
        ids.append(receipt.id)
    await db.commit()
    return conversation_id, run.id, ids


async def test_committed_receipt_changes_are_ordered_private_and_idempotent(
    db: AsyncSession, receipts: tuple[UUID, UUID, list[UUID]]
) -> None:
    conversation_id, run_id, ids = receipts
    context = unit_context(TEST_USER_ID)
    feed = RunSteerReceiptFeed(context, conversation_id, run_id)

    admitted = await feed.changed_events(first_seq=5, force=True)
    assert [event["seq"] for event in admitted] == [5, 6]
    assert [event["data"] for event in admitted] == [
        {
            "name": "moldy.run_steer",
            "payload": {
                "conversation_id": str(conversation_id),
                "run_id": str(run_id),
                "steer_id": str(steer_id),
                "status": "pending",
            },
        }
        for steer_id in ids
    ]
    assert await feed.changed_events(first_seq=7, force=True) == []
    fresh = RunSteerReceiptFeed(context, conversation_id, run_id)
    again = await fresh.changed_events(first_seq=20, force=True)
    assert [event["id"] for event in again] == [event["id"] for event in admitted]

    claimed = await conversation_run_steers.claim_steers_for_model(
        db, conversation_id=conversation_id, run_id=run_id
    )
    assert [row.id for row in claimed] == ids
    await conversation_run_steers.confirm_applied_steers(
        db, conversation_id=conversation_id, run_id=run_id, steer_ids=[ids[0]]
    )
    await db.commit()
    applied = await feed.changed_events(first_seq=7, force=True)
    assert len(applied) == 1
    assert applied[0]["data"] == {
        "name": "moldy.run_steer",
        "payload": {**admitted[0]["data"]["payload"], "status": "applied"},
    }
    assert applied[0]["id"] != admitted[0]["id"]
    assert await feed.changed_events(first_seq=8, force=True) == []

    for scope, conversation, run in [
        (replace(context, user_id=uuid4()), conversation_id, run_id),
        (replace(context, org_id=uuid4()), conversation_id, run_id),
        (replace(context, tenant_id=uuid4()), conversation_id, run_id),
        (context, uuid4(), run_id),
        (context, conversation_id, uuid4()),
    ]:
        other = RunSteerReceiptFeed(scope, conversation, run)
        assert await other.changed_events(first_seq=1, force=True) == []


async def test_live_receipts_replay_from_committed_chunks_without_a_second_run(
    db: AsyncSession, receipts: tuple[UUID, UUID, list[UUID]]
) -> None:
    conversation_id, run_id, ids = receipts
    context = unit_context(TEST_USER_ID)
    feed = RunSteerReceiptFeed(context, conversation_id, run_id)

    async def store(events: list[dict[str, Any]]) -> None:
        async with TestSession() as writer:
            await trace_storage.append_events(
                writer,
                conversation_id=conversation_id,
                assistant_msg_id=str(run_id),
                events_chunk=events,
            )
            await writer.commit()

    chunks = [
        sse_payload(chunk)
        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([]),
            {"messages": []},
            {"configurable": {"thread_id": str(conversation_id)}},
            run_id=str(run_id),
            steer_receipts=feed,
            persist_callback=store,
        )
    ]
    assert chunks[0]["params"]["data"] == {"event": "running"}
    assert chunks[-1]["params"]["data"] == {"event": "completed"}
    assert [event["seq"] for event in chunks] == sorted({event["seq"] for event in chunks})
    live = [event for event in chunks if event["method"] == "custom"]
    assert [event["params"]["data"]["payload"]["steer_id"] for event in live] == [
        str(item) for item in ids
    ]

    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    events = await load_live_protocol_events(
        LiveProtocolEventsRequest(db, conversation, context, secret_values=())
    )
    replay = [
        sse_payload(chunk)
        async for chunk in protocol_replay_generator(
            events, {"channels": ["custom:moldy.run_steer"]}, after_id=live[0]["event_id"]
        )
    ]
    assert len(replay) == 1
    assert replay[0]["params"] == live[1]["params"]
    assert replay[0]["event_id"] == live[1]["event_id"]


@pytest.mark.parametrize("fallback", [False, True], ids=["v3", "stream-mode"])
async def test_graph_custom_events_cannot_impersonate_a_steer_receipt(fallback: bool) -> None:
    forged = {"status": "applied", "steer_id": str(uuid4()), "text": "Untrusted instruction"}
    events = [
        ("custom:moldy.run_steer", forged),
        ("custom", {"name": "moldy.run_steer", "payload": forged}),
        ("custom", {"channel": "moldy.run_steer", "payload": forged}),
        ("custom:tool-progress", {"value": 1}),
    ]
    if fallback:
        agent = FallbackAgent(events)
    else:
        agent = ProtocolAgent(
            [
                {"method": method, "params": {"data": data}, "seq": index}
                for index, (method, data) in enumerate(events, 1)
            ]
        )
    chunks = [
        sse_payload(chunk)
        async for chunk in stream_agent_response_langgraph(
            agent, {"messages": []}, {"configurable": {"thread_id": "thread"}}, run_id="run"
        )
    ]
    assert [chunk["params"]["data"] for chunk in chunks if chunk["method"] == "custom"] == [
        {"name": "tool-progress", "payload": {"value": 1}}
    ]


def test_historical_custom_notifications_require_valid_receipt_metadata() -> None:
    conversation_id, run_id = uuid4(), uuid4()
    record = MessageEvent(conversation_id=conversation_id, assistant_msg_id=str(run_id), events=[])
    old_data = {"channel": "moldy.run_steer", "payload": {"status": "pending", "text": "old"}}
    flat = {"id": "old-flat", "seq": 1, "method": "custom:moldy.run_steer", "data": old_data}
    wire = {
        "type": "event",
        "event_id": "old-wire",
        "seq": 2,
        "method": "custom",
        "params": {"data": old_data},
    }
    for raw in (flat, wire):
        assert stored_events_from_raw(RawEventProjectionRequest(raw, record, 1)) == []

    ordinary = {**flat, "method": "custom:tool-progress", "data": {"value": 1}}
    projected = stored_events_from_raw(RawEventProjectionRequest(ordinary, record, 1))
    assert len(projected) == 1
    assert projected[0]["data"] == {"name": "tool-progress", "payload": {"value": 1}}


async def test_snapshot_orders_admission_and_application_by_durable_sequence_across_workers(
    db: AsyncSession, receipts: tuple[UUID, UUID, list[UUID]]
) -> None:
    conversation_id, run_id, ids = receipts
    context = unit_context(TEST_USER_ID)
    for index, steer_id in enumerate(ids):
        row = await db.get(ConversationRunSteer, steer_id)
        assert row is not None
        # The worker acknowledging the checkpoint has a clock behind the API accepting it.
        # Two API workers' clocks also disagree with the database admission sequence.
        row.created_at = datetime(2026, 11, 25 - index, 10)
        row.delivery_started_at = datetime(2026, 11, 25 - index, 11)
        row.applied_at = datetime(2026, 11, 24 - index, 12)
        row.status = "applied"
    await db.commit()

    snapshot = await RunSteerReceiptFeed(context, conversation_id, run_id).changed_events(
        first_seq=5, force=True
    )
    ordered = [event["data"]["payload"] for event in snapshot]
    assert [(item["status"], item["steer_id"]) for item in ordered] == [
        (status, str(steer_id)) for status in ("pending", "applied") for steer_id in ids
    ]


async def test_receipt_committed_after_upstream_root_terminal_stays_outside_closed_stream(
    db: AsyncSession, receipts: tuple[UUID, UUID, list[UUID]]
) -> None:
    conversation_id, run_id, ids = receipts
    context = unit_context(TEST_USER_ID)
    agent = ProtocolAgent(
        [
            {"method": "lifecycle", "params": {"data": {"event": "completed"}}, "seq": 1},
            {"method": "values", "params": {"data": {"todos": []}}, "seq": 2},
        ]
    )
    chunks = []
    terminal_index = None
    late_id = None
    async for chunk in stream_agent_response_langgraph(
        agent,
        {"messages": []},
        {"configurable": {"thread_id": str(conversation_id)}},
        run_id=str(run_id),
        steer_receipts=RunSteerReceiptFeed(context, conversation_id, run_id),
    ):
        event = sse_payload(chunk)
        chunks.append(event)
        if (
            event["method"] == "lifecycle"
            and event["params"]["data"]["event"] == "completed"
            and terminal_index is None
        ):
            terminal_index = len(chunks) - 1
            late = await conversation_run_steers.admit_steer(
                db,
                conversation_id=conversation_id,
                run_id=run_id,
                context=context,
                client_request_id=uuid4(),
                text="Additional source",
            )
            await db.commit()
            late_id = late.id
    assert late_id is not None and terminal_index is not None
    assert not any(event["method"] == "custom" for event in chunks[terminal_index:])
    seen = [
        event["params"]["data"]["payload"]["steer_id"]
        for event in chunks
        if event["method"] == "custom"
    ]
    assert seen == [str(steer_id) for steer_id in ids]
    late_row = await db.get(ConversationRunSteer, late_id)
    assert late_row is not None and late_row.status == "pending"


async def test_recovery_checks_owned_rows_and_suppresses_only_committed_receipts(
    db: AsyncSession, receipts: tuple[UUID, UUID, list[UUID]]
) -> None:
    from app.agent_runtime.recovery_stream import (
        RecoveryOutputStarted,
        RecoveryStreamCursor,
        continue_protocol_delivery,
    )
    from app.services.conversation_recovery_steers import recovery_steers

    conversation_id, run_id, ids = receipts
    context = unit_context(TEST_USER_ID)
    first = RunSteerReceiptFeed(context, conversation_id, run_id)
    pending = [dict(item) for item in await first.changed_events(first_seq=1, force=True)]
    scoped = await recovery_steers(db, context, run_id=run_id, conversation_id=conversation_id)
    foreign = await recovery_steers(
        db, replace(context, user_id=uuid4()), run_id=run_id, conversation_id=conversation_id
    )
    for invalid_context, thread, run in (
        (foreign, conversation_id, run_id),
        (scoped, uuid4(), run_id),
        (scoped, conversation_id, uuid4()),
    ):
        with pytest.raises(RecoveryOutputStarted):
            invalid_context.public_ids(pending, thread, run)
    await conversation_run_steers.claim_steers_for_model(
        db, conversation_id=conversation_id, run_id=run_id
    )
    await conversation_run_steers.confirm_applied_steers(
        db, conversation_id=conversation_id, run_id=run_id, steer_ids=ids
    )
    await db.commit()
    applied = [dict(item) for item in await first.changed_events(first_seq=3, force=True)]
    with pytest.raises(RecoveryOutputStarted):
        scoped.public_ids(pending + applied, conversation_id, run_id)
    proof = await recovery_steers(db, context, run_id=run_id, conversation_id=conversation_id)
    with pytest.raises(RecoveryOutputStarted):
        proof.public_ids(applied, conversation_id, run_id)
    cursor = RecoveryStreamCursor.from_events(
        str(run_id),
        str(conversation_id),
        pending,
        verified_steer_events=proof.public_ids(pending, conversation_id, run_id),
        claimed_steers=proof.claimed,
    )
    feed = RunSteerReceiptFeed.for_chat(
        context, str(conversation_id), str(run_id), source="recovery", profile="standard"
    )
    assert feed is not None
    with continue_protocol_delivery(cursor):
        chunks = [
            sse_payload(chunk)
            async for chunk in stream_agent_response_langgraph(
                ProtocolAgent([]),
                None,
                {"configurable": {"thread_id": str(conversation_id)}},
                run_id=str(run_id),
                steer_receipts=feed,
            )
        ]
    actual = [chunk["event_id"] for chunk in chunks if chunk["method"] == "custom"]
    assert actual == [f"{key}:steer:applied" for key in ids]


def test_steer_in_values_does_not_hide_a_prior_response_or_tool_call() -> None:
    from app.agent_runtime.recovery_stream import state_output_proof
    from app.schemas.conversation_graph_input import STEER_INPUT_MARKER

    human = {"type": "human", "id": "original", "content": "research"}
    steer = {"type": "human", "content": "revise", "response_metadata": {STEER_INPUT_MARKER: True}}
    assert state_output_proof(
        {"messages": [human, {"type": "ai", "content": "visible"}, steer]}, set()
    )
    calls: set[str] = set()
    assert not state_output_proof(
        {"messages": [human, {"type": "ai", "tool_calls": [{"id": "read"}]}, steer]}, calls
    )
    assert calls == {"read"}
