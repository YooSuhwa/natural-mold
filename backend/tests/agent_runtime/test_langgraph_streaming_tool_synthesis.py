from __future__ import annotations

from typing import Any

import pytest

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph


class ProtocolAgent:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def astream_events(self, *_args: Any, **_kwargs: Any) -> Any:
        async def _stream() -> Any:
            for event in self.events:
                yield event

        return _stream()


class FakeArtifactRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def prepare(self) -> None:
        return None

    async def collect_after_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> list[dict[str, object]]:
        self.calls.append((tool_name, tool_call_id))
        return [{"op": "created", "id": "artifact-1", "path": "report.md"}]


@pytest.mark.asyncio
async def test_values_tool_message_drives_artifact_side_effects() -> None:
    raw_event = {
        "type": "event",
        "method": "values",
        "params": {
            "namespace": [],
            "data": {
                "messages": [
                    {
                        "type": "ai",
                        "tool_calls": [
                            {
                                "id": "call-docx",
                                "name": "execute_in_skill",
                                "args": {"command": "node create_report.cjs"},
                            }
                        ],
                    },
                    {
                        "type": "tool",
                        "name": "execute_in_skill",
                        "tool_call_id": "call-docx",
                        "content": "OUTPUT_FILES: report.md",
                        "status": "success",
                    }
                ]
            },
        },
        "seq": 1,
        "event_id": "values-1",
    }
    recorder = FakeArtifactRecorder()

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([raw_event]),
            {"messages": []},
            {"configurable": {"thread_id": "thread-artifacts"}},
            artifact_recorder=recorder,
            run_id="run-artifacts",
        )
    ]

    assert recorder.calls == [("execute_in_skill", "call-docx")]
    assert any("custom:file_event" in chunk for chunk in chunks)
