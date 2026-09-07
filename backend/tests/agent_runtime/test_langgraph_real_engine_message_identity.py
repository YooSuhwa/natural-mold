from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime import langgraph_agent_stream_runner, langgraph_streaming
from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
from app.agent_runtime.langgraph_message_identity import collect_root_ai_message_id
from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.runtime_config import AgentConfig

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)

RUN_ID = "11111111-1111-4111-8111-111111111111"
THREAD_ID = "22222222-2222-4222-8222-222222222222"


class SafeEventShape(TypedDict):
    method: str
    namespace_length: int
    data_type: str
    data_keys: tuple[str, ...]
    sequence_length: int | None
    item_types: tuple[str, ...]
    message_type: str | None
    has_id: bool
    id_hash: str | None
    id_equals_run: bool | None


class SafeStateMessageShape(TypedDict):
    data_type: str
    data_keys: tuple[str, ...]
    role_is_assistant: bool
    has_id: bool
    id_hash: str | None


def _id_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _safe_event_shape(event: StoredProtocolEvent) -> SafeEventShape:
    data = event["data"]
    data_keys = tuple(sorted(str(key) for key in data)) if isinstance(data, Mapping) else ()
    is_sequence = isinstance(data, Sequence) and not isinstance(data, str | bytes | bytearray)
    raw_id = data.get("id") if isinstance(data, Mapping) else None
    message_type = data.get("type") if isinstance(data, Mapping) else None
    return {
        "method": event["method"],
        "namespace_length": len(event["namespace"]),
        "data_type": type(data).__name__,
        "data_keys": data_keys,
        "sequence_length": len(data) if is_sequence else None,
        "item_types": tuple(type(item).__name__ for item in data) if is_sequence else (),
        "message_type": message_type if isinstance(message_type, str) else None,
        "has_id": isinstance(raw_id, str) and bool(raw_id),
        "id_hash": _id_hash(raw_id) if isinstance(raw_id, str) and raw_id else None,
        "id_equals_run": raw_id == RUN_ID if isinstance(raw_id, str) else None,
    }


def _safe_state_message_shapes(event: StoredProtocolEvent) -> list[SafeStateMessageShape]:
    data = event["data"]
    if event["method"] != "values" or not isinstance(data, Mapping):
        return []
    messages = data.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes | bytearray):
        return []
    shapes: list[SafeStateMessageShape] = []
    for message in messages:
        keys = tuple(sorted(str(key) for key in message)) if isinstance(message, Mapping) else ()
        raw_id = message.get("id") if isinstance(message, Mapping) else None
        role = message.get("type") if isinstance(message, Mapping) else None
        shapes.append(
            {
                "data_type": type(message).__name__,
                "data_keys": keys,
                "role_is_assistant": role in {"ai", "AIMessage", "AIMessageChunk"},
                "has_id": isinstance(raw_id, str) and bool(raw_id),
                "id_hash": _id_hash(raw_id) if isinstance(raw_id, str) and raw_id else None,
            }
        )
    return shapes


@pytest.mark.asyncio
async def test_real_compiled_graph_links_its_emitted_assistant_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = E2EScriptedChatModel(slow_stream_delay_seconds=0)
    monkeypatch.setattr(
        "app.agent_runtime.runtime_component_builder._build_model_candidates",
        lambda _cfg: [model],
    )
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", MemorySaver)

    observed_shapes: list[SafeEventShape] = []
    stream_ids: list[str] = []
    final_state_shapes: list[SafeStateMessageShape] = []
    final_state_ids: list[str] = []

    def observe_identity(event: StoredProtocolEvent, sink: list[str] | None) -> None:
        if event["method"] == "messages":
            observed_shapes.append(_safe_event_shape(event))
            data = event["data"]
            raw_id = data.get("id") if isinstance(data, Mapping) else None
            if isinstance(raw_id, str) and raw_id:
                stream_ids.append(raw_id)
        state_shapes = _safe_state_message_shapes(event)
        if state_shapes:
            final_state_shapes[:] = state_shapes
            data = event["data"]
            messages = data.get("messages") if isinstance(data, Mapping) else None
            if isinstance(messages, Sequence) and not isinstance(messages, str | bytes | bytearray):
                final_state_ids[:] = [
                    raw_id
                    for message in messages
                    if isinstance(message, Mapping)
                    and isinstance((raw_id := message.get("id")), str)
                    and raw_id
                ]
        collect_root_ai_message_id(event, sink)

    monkeypatch.setattr(langgraph_streaming, "collect_root_ai_message_id", observe_identity)
    cfg = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="Return the deterministic response.",
        tools_config=[],
        thread_id=THREAD_ID,
    )
    msg_id_sink: list[str] = []

    _ = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            cfg,
            [{"role": "user", "content": "durable run summary verification"}],
            msg_id_sink=msg_id_sink,
            run_id=RUN_ID,
        )
    ]

    assert stream_ids
    root_stream_id = stream_ids[0]
    target_canonical = str(parse_msg_id(root_stream_id, uuid.UUID(THREAD_ID), 0))
    canonical_sink = {
        str(parse_msg_id(raw_id, uuid.UUID(THREAD_ID), index))
        for index, raw_id in enumerate(msg_id_sink)
    }
    safe_diagnostic = {
        "message_shapes": observed_shapes,
        "final_state_message_shapes": final_state_shapes,
        "stream_id_hash": _id_hash(root_stream_id),
        "final_state_id_hashes": [_id_hash(raw_id) for raw_id in final_state_ids],
        "stream_id_present_in_final_state": root_stream_id in final_state_ids,
        "sink_count": len(msg_id_sink),
        "sink_non_run_count": sum(raw_id != RUN_ID for raw_id in msg_id_sink),
        "target_canonical_equals_run": target_canonical == RUN_ID,
        "target_canonical_present_in_sink": target_canonical in canonical_sink,
    }

    if root_stream_id not in final_state_ids or root_stream_id not in msg_id_sink:
        pytest.fail(json.dumps(safe_diagnostic, sort_keys=True), pytrace=False)
