from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.models.message_event import MessageEvent
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.routers.conversation_agent_protocol_runtime import protocol_events_from_broker


def _event_payloads(chunks: list[str]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                loaded = json.loads(line.removeprefix("data: "))
                if isinstance(loaded, dict):
                    payloads.append(loaded)
    return payloads


async def _collect_replay_chunks(events: list[StoredProtocolEvent]) -> list[str]:
    chunks: list[str] = []
    replay: AsyncIterator[str] = protocol_replay_generator(
        events,
        {"channels": ["custom:usage"]},
        after_id=None,
    )
    async for chunk in replay:
        chunks.append(chunk)
    return chunks


@pytest.mark.asyncio
async def test_replay_normalizes_legacy_flat_custom_prefixed_event(db: AsyncSession) -> None:
    conversation_id = uuid.uuid4()
    run_id = "run-flat-custom"
    db.add(
        MessageEvent(
            conversation_id=conversation_id,
            assistant_msg_id=run_id,
            events=[
                {
                    "id": "usage-flat-1",
                    "upstream_event_id": "usage-flat-1",
                    "seq": 7,
                    "method": "custom:usage",
                    "namespace": [],
                    "data": {"prompt_tokens": 3, "completion_tokens": 5},
                    "run_id": run_id,
                    "thread_id": str(conversation_id),
                    "timestamp": "2026-06-14T00:00:00+00:00",
                }
            ],
            last_event_id="usage-flat-1",
            status="completed",
        )
    )
    await db.commit()

    events = await load_protocol_events(db, conversation_id)
    chunks = await _collect_replay_chunks(events)
    payloads = _event_payloads(chunks)

    assert len(payloads) == 1
    event = payloads[0]
    assert event["method"] == "custom"
    params = event["params"]
    assert isinstance(params, dict)
    data = params["data"]
    assert data == {
        "name": "usage",
        "payload": {"prompt_tokens": 3, "completion_tokens": 5},
    }


@pytest.mark.asyncio
async def test_replay_normalizes_legacy_wire_custom_prefixed_event(db: AsyncSession) -> None:
    conversation_id = uuid.uuid4()
    run_id = "run-wire-custom"
    db.add(
        MessageEvent(
            conversation_id=conversation_id,
            assistant_msg_id=run_id,
            events=[
                {
                    "type": "event",
                    "method": "custom:usage",
                    "seq": 11,
                    "event_id": "usage-wire-1",
                    "params": {
                        "namespace": [],
                        "data": {"prompt_tokens": 8, "completion_tokens": 13},
                        "timestamp": "2026-06-14T00:00:01+00:00",
                        "checkpoint_id": "ck-wire",
                        "checkpoint_ns": "model",
                    },
                }
            ],
            last_event_id="usage-wire-1",
            status="completed",
        )
    )
    await db.commit()

    events = await load_protocol_events(db, conversation_id)
    chunks = await _collect_replay_chunks(events)
    payloads = _event_payloads(chunks)

    assert len(payloads) == 1
    event = payloads[0]
    assert event["method"] == "custom"
    params = event["params"]
    assert isinstance(params, dict)
    assert params["checkpoint_id"] == "ck-wire"
    assert params["checkpoint_ns"] == "model"
    data = params["data"]
    assert data == {
        "name": "usage",
        "payload": {"prompt_tokens": 8, "completion_tokens": 13},
    }


def test_live_broker_normalizes_legacy_custom_prefixed_event() -> None:
    events = protocol_events_from_broker(
        {
            "id": "broker-custom-1",
            "event": "message",
            "data": {
                "type": "event",
                "method": "custom:usage",
                "seq": 3,
                "event_id": "usage-broker-1",
                "params": {
                    "namespace": [],
                    "data": {"prompt_tokens": 21, "completion_tokens": 34},
                    "timestamp": "2026-06-14T00:00:02+00:00",
                    "checkpoint_id": "ck-broker",
                    "checkpoint_ns": "model",
                },
            },
        },
        run_id="run-broker-custom",
        thread_id="thread-broker-custom",
    )

    assert len(events) == 1
    event = events[0]
    assert event["method"] == "custom"
    assert event["checkpoint_id"] == "ck-broker"
    assert event["checkpoint_ns"] == "model"
    assert event["data"] == {
        "name": "usage",
        "payload": {"prompt_tokens": 21, "completion_tokens": 34},
    }


def test_custom_prefix_normalization_does_not_double_wrap_named_payload() -> None:
    events = protocol_events_from_broker(
        {
            "id": "broker-custom-named-1",
            "event": "message",
            "data": {
                "type": "event",
                "method": "custom:usage",
                "seq": 4,
                "event_id": "usage-broker-named-1",
                "params": {
                    "namespace": [],
                    "data": {
                        "name": "custom:usage",
                        "payload": {"prompt_tokens": 55, "completion_tokens": 89},
                    },
                },
            },
        },
        run_id="run-broker-custom",
        thread_id="thread-broker-custom",
    )

    assert len(events) == 1
    assert events[0]["data"] == {
        "name": "usage",
        "payload": {"prompt_tokens": 55, "completion_tokens": 89},
    }
