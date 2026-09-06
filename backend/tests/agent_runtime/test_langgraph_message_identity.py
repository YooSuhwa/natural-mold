from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.agent_runtime.langgraph_message_identity import collect_root_ai_message_id
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event


def _event(
    data: Mapping[str, object],
    *,
    method: str = "messages",
    namespace: list[str] | None = None,
) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=1,
        method=method,
        namespace=namespace,
        data=dict(data),
    )


@pytest.mark.parametrize(
    ("data", "expected_id"),
    [
        ({"event": "message-start", "role": "ai", "id": "v3-ai"}, "v3-ai"),
        ({"type": "ai", "id": "legacy-ai"}, "legacy-ai"),
        ({"type": "AIMessageChunk", "id": "legacy-chunk"}, "legacy-chunk"),
    ],
)
def test_collects_supported_root_ai_identity_once(
    data: Mapping[str, object],
    expected_id: str,
) -> None:
    sink = ["run-1"]
    event = _event(data)

    collect_root_ai_message_id(event, sink)
    collect_root_ai_message_id(event, sink)

    assert sink == ["run-1", expected_id]


@pytest.mark.parametrize(
    ("event", "sink"),
    [
        (_event({"event": "message-finish", "role": "ai", "id": "non-start"}), []),
        (_event({"event": "message-start", "role": "human", "id": "user"}), []),
        (_event({"event": "message-start", "role": "tool", "id": "tool"}), []),
        (_event({"event": "message-start", "role": "assistant", "id": "unknown"}), []),
        (_event({"event": "message-start", "role": "ai", "id": "child"}, namespace=["child"]), []),
        (_event({"event": "message-start", "role": "ai", "id": "history"}, method="values"), []),
        (_event({"event": "message-finish", "role": "ai", "type": "ai", "id": "mixed"}), []),
        (_event({"role": "ai", "type": "ai", "id": "malformed"}), []),
    ],
)
def test_rejects_non_root_non_assistant_or_non_start_identity(
    event: StoredProtocolEvent,
    sink: list[str],
) -> None:
    collect_root_ai_message_id(event, sink)

    assert sink == []


def test_keeps_v3_start_identity_when_a_later_failure_arrives() -> None:
    sink = ["run-1"]
    collect_root_ai_message_id(
        _event({"event": "message-start", "role": "ai", "id": "partial-ai"}),
        sink,
    )

    collect_root_ai_message_id(
        _event({"event": "error", "role": "ai", "id": "partial-ai"}, method="error"),
        sink,
    )

    assert sink == ["run-1", "partial-ai"]
