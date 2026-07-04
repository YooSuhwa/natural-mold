from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from tests.agent_runtime.langgraph_streaming_fixtures import (
    FakeArtifactRecorder,
    ProtocolAgent,
    sse_payload,
)


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_file_event_after_artifact_tool_result() -> None:
    raw_event = {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "data": {
                "event": "tool-finished",
                "tool_name": "execute_in_skill",
                "tool_call_id": "call-1",
                "output": "done",
            },
        },
        "seq": 1,
        "event_id": "tool-finished-1",
    }
    recorder = FakeArtifactRecorder()
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-artifacts"}},
            artifact_recorder=recorder,
            run_id="run-artifacts",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert recorder.prepared is True
    assert recorder.calls == [("execute_in_skill", "call-1")]
    # W2-4 — execute_in_skill 결과는 file_event에 더해 terminal ui_data
    # custom 이벤트도 투영된다 (UI_DATA_TOOL_TRANSFORMERS).
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "tools",
        "custom",
        "custom",
        "lifecycle",
    ]
    assert payloads[2]["params"]["data"] == {
        "name": "file_event",
        "payload": {
            "op": "created",
            "id": "artifact-1",
            "path": "report.md",
        },
    }
    ui_data = payloads[3]["params"]["data"]
    # side-effect 관례상 custom name은 무접두 "ui_data" (프론트가 양형 정규화).
    assert ui_data["name"] == "ui_data"
    assert ui_data["payload"]["type"] == "terminal"
    assert ui_data["payload"]["props"] == {"lines": "done"}
    assert ui_data["payload"]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_ui_data_event_after_demo_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The demo tool is only recognized when the scripted model is enabled.
    from app.config import settings

    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    raw_event: dict[str, Any] = {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "data": {
                "event": "tool-finished",
                "tool_name": "e2e_ui_data_demo",
                "tool_call_id": "ui-data-call-1",
                "output": json.dumps({"ui_type": "demo_note", "text": "demo note"}),
            },
        },
        "seq": 1,
        "event_id": "ui-data-tool-finished-1",
    }
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-ui-data"}},
            run_id="run-ui-data",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "tools",
        "custom",
        "lifecycle",
    ]
    assert payloads[2]["params"]["data"] == {
        "name": "ui_data",
        "payload": {
            "schema_version": 1,
            "type": "demo_note",
            "message_id": None,
            "run_id": "run-ui-data",
            "tool_call_id": "ui-data-call-1",
            "props": {"text": "demo note"},
        },
    }


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_memory_event_after_memory_tool_result() -> None:
    raw_event: dict[str, Any] = {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "data": {
                "event": "tool-finished",
                "tool_name": "save_user_memory",
                "tool_call_id": "memory-call-1",
                "output": json.dumps(
                    {
                        "memory_event": "memory_saved",
                        "id": "memory-1",
                        "scope": "user",
                        "content": "User prefers concise answers.",
                        "reason": None,
                        "policy": "auto",
                    }
                ),
            },
        },
        "seq": 1,
        "event_id": "memory-tool-finished-1",
    }
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-memory"}},
            run_id="run-memory",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "tools",
        "custom",
        "lifecycle",
    ]
    assert payloads[2]["params"]["data"] == {
        "name": "memory_saved",
        "payload": {
            "id": "memory-1",
            "scope": "user",
            "content": "User prefers concise answers.",
            "reason": None,
            "policy": "auto",
        },
    }
