from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any


class ProtocolAgent:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.inputs: list[Any] = []

    async def astream_events(self, input_: Any, **_kwargs: Any) -> Any:
        self.inputs.append(input_)

        async def _stream() -> Any:
            for event in self.events:
                yield event

        return _stream()


class StateBackedProtocolAgent(ProtocolAgent):
    def __init__(self, events: list[dict[str, Any]], state: SimpleNamespace) -> None:
        super().__init__(events)
        self.state = state

    async def aget_state(self, _config: dict[str, Any]) -> SimpleNamespace:
        return self.state


class FallbackAgent:
    def __init__(self, chunks: list[tuple[str, Any]]) -> None:
        self.chunks = chunks

    async def astream_events(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("v3 unavailable")

    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        for chunk in self.chunks:
            yield chunk


class MidStreamAttributeErrorAgent:
    def __init__(self) -> None:
        self.fallback_calls = 0

    async def astream_events(self, *_args: Any, **_kwargs: Any) -> Any:
        async def _stream() -> Any:
            yield {
                "type": "event",
                "method": "messages",
                "params": {"namespace": [], "data": {"chunk": "started"}},
                "seq": 1,
                "event_id": "midstream-1",
            }
            raise AttributeError("adapter bug after stream opened")

        return _stream()

    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        self.fallback_calls += 1
        yield ("values", {"messages": [], "todos": []})


class ErrorAgent:
    async def astream_events(self, *_args: Any, **_kwargs: Any) -> Any:
        async def _stream() -> Any:
            raise RuntimeError("stream failed")
            yield {}

        return _stream()


class FakeArtifactRecorder:
    def __init__(self) -> None:
        self.prepared = False
        self.calls: list[tuple[str, str | None]] = []

    async def prepare(self) -> None:
        self.prepared = True

    async def collect_after_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> list[dict[str, object]]:
        self.calls.append((tool_name, tool_call_id))
        return [{"op": "created", "id": "artifact-1", "path": "report.md"}]


def sse_payload(raw: str) -> dict[str, Any]:
    data_line = next(line for line in raw.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return payload
