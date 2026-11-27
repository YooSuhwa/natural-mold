"""Emit AG-UI SSE from persisted inputs for the cross-language stream contract."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from ag_ui.core import Event
from ag_ui.encoder import EventEncoder
from pydantic import TypeAdapter

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector, ChildVisibility
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from tests.agent_runtime.test_delegated_chat_replay import _current_journal

THREAD = "10000000-0000-4000-8000-000000000032"
SOURCE_RUN = "40000000-0000-4000-8000-000000000032"
PUBLIC_RUN = "30000000-0000-4000-8000-000000000001"
ARCHIVE = Path(__file__).with_name("fixtures") / "delegated_chat_0_7_32.jsonl"
A = ["tools:fixture-alpha"]
B = ["tools:fixture-beta"]
Row = tuple[str, list[str], dict[str, Any]]
PUBLIC_STREAM = TypeAdapter(list[Event])


def journal(rows: Iterable[Row]) -> list[StoredProtocolEvent]:
    return [
        stored_protocol_event(
            thread_id=THREAD, run_id=SOURCE_RUN, seq=i, method=method, namespace=ns, data=data
        )
        for i, (method, ns, data) in enumerate(rows, start=1)
    ]


def siblings() -> list[StoredProtocolEvent]:
    """Same task name and raw child model ID across two interleaved invocations."""
    rows: list[Row] = [("lifecycle", [], {"event": "running"})]
    calls = [
        {"type": "tool_call", "name": "task", "id": call_id, "args": {"description": brief}}
        for call_id, brief in (("call-a", "REVOKED_BRIEF"), ("call-b", "두 번째 질문"))
    ]
    rows.append(
        ("messages", [], {"type": "ai", "id": "issuer", "content": "", "tool_calls": calls})
    )
    for ns, call in zip((A, B), calls, strict=True):
        rows.append(
            (
                "tools",
                [],
                {
                    "event": "tool-started",
                    "tool_call_id": call["id"],
                    "name": "task",
                    "args": call["args"],
                },
            )
        )
        rows.append(
            (
                "lifecycle",
                [],
                {
                    "event": "started",
                    "namespace": ns,
                    "graph_name": "researcher",
                    "cause": {"type": "toolCall", "tool_call_id": call["id"]},
                },
            )
        )
    for ns, part in ((A, "첫째 "), (B, "둘째 "), (A, "응답"), (B, "응답")):
        rows.append(
            ("messages", ns, {"id": "provider-child", "type": "AIMessageChunk", "content": part})
        )
    for ns, call_id, result in ((B, "call-b", "허용된 결과"), (A, "call-a", "REVOKED_RESULT")):
        rows.append(("lifecycle", [], {"event": "completed", "namespace": ns}))
        rows.append(
            (
                "tools",
                [],
                {
                    "event": "tool-finished",
                    "tool_call_id": call_id,
                    "name": "task",
                    "status": "complete",
                    "content": result,
                },
            )
        )
    rows.extend(
        [
            ("values", [], {"files": {"/private": "PRIVATE_STATE_CANARY"}}),
            (
                "custom",
                [],
                {"name": "unknown_backend_event", "payload": {"trace": "PRIVATE_FRAME_CANARY"}},
            ),
            (
                "messages",
                [],
                {
                    "type": "ai",
                    "id": "last-answer",
                    "content": [
                        {"type": "reasoning", "reasoning": "PRIVATE_REASONING_CANARY"},
                        {"type": "text", "text": "마침"},
                    ],
                },
            ),
            ("lifecycle", [], {"event": "completed"}),
        ]
    )
    return journal(rows)


def approvals() -> list[StoredProtocolEvent]:
    action = {
        "action_requests": [{"name": "send_message", "args": {"subject": "가상 사례"}}],
        "review_configs": [
            {"action_name": "send_message", "allowed_decisions": ["approve", "reject"]}
        ],
    }
    rows: list[Row] = [("lifecycle", [], {"event": "running"})]
    for iid in ("confirm-first", "confirm-second"):
        rows.append(
            ("input.requested", [], {"interrupt_id": iid, "payload": action, "namespace": []})
        )
    rows.append(("lifecycle", [], {"event": "interrupted"}))
    return journal(rows)


def activities() -> list[StoredProtocolEvent]:
    records = journal(
        [
            ("lifecycle", [], {"event": "running"}),
            (
                "values",
                [],
                {
                    "todos": [{"content": "정리", "status": "completed"}],
                    "files": {"/private": "PRIVATE_STATE_CANARY"},
                },
            ),
            (
                "messages",
                [],
                {
                    "type": "ai",
                    "id": "billed-message",
                    "content": "정리했어요.",
                    "usage_metadata": {"input_tokens": 9, "output_tokens": 3},
                },
            ),
        ]
    )
    steer = "60000000-0000-4000-8000-000000000001"
    for status in ("pending", "applied"):
        records.append(
            stored_custom_protocol_event(
                run_id=SOURCE_RUN,
                thread_id=THREAD,
                seq=len(records) + 1,
                name="moldy.run_steer",
                event_id=f"{steer}:steer:{status}",
                payload={
                    "conversation_id": THREAD,
                    "run_id": SOURCE_RUN,
                    "steer_id": steer,
                    "status": status,
                },
            )
        )
    for name, payload in (
        ("compaction", {"state": "running"}),
        ("compaction", {"state": "done", "summary_message": "PRIVATE_SUMMARY_CANARY"}),
        (
            "memory_recalled",
            {
                "memories": [
                    {"id": "test-memory", "scope": "user", "content": "PRIVATE_MEMORY_CANARY"}
                ]
            },
        ),
        (
            "usage",
            {"assistant_msg_id": "billed-message", "prompt_tokens": 9, "completion_tokens": 3},
        ),
    ):
        records.append(
            stored_custom_protocol_event(
                thread_id=THREAD,
                run_id=SOURCE_RUN,
                seq=len(records) + 1,
                name=f"moldy.{name}",
                payload=payload,
            )
        )
    return records


def project(
    records: Sequence[StoredProtocolEvent],
    *,
    run_id: str = PUBLIC_RUN,
    final_status: str,
    child_visibility: ChildVisibility | None = None,
) -> str:
    projector = AgUiV1Projector(
        thread_id=THREAD, run_id=run_id, source_run_id=SOURCE_RUN, child_visibility=child_visibility
    )
    encoder = EventEncoder()
    frames = [frame for record in records for frame in projector.project(record)]
    frames.extend(projector.finish(final_status))
    assert projector.closed
    decoded = PUBLIC_STREAM.validate_python([frame.model_dump(by_alias=True) for frame in frames])
    assert decoded == frames
    return "".join(encoder.encode(frame) for frame in frames)


async def cases() -> dict[str, object]:
    archived = [
        cast(StoredProtocolEvent, json.loads(row)) for row in ARCHIVE.read_text().splitlines()
    ]
    installed = cast(list[StoredProtocolEvent], await _current_journal())
    assert any(e["method"] == "tools" for e in installed)
    concurrent = siblings()
    complete = journal([("lifecycle", [], {"event": "completed"})])
    failed = journal([("error", [], {"message": "PRIVATE_ERROR_CANARY"})])
    passes = {
        "archived": project(archived, final_status="completed"),
        "installed": project(installed, final_status="completed"),
        "siblings": project(concurrent, final_status="completed"),
        "restricted": project(
            concurrent,
            final_status="completed",
            child_visibility=ChildVisibility(
                allow_legacy_details=False,
                tool_call_ids=frozenset({"call-b"}),
                namespaces=frozenset({tuple(B)}),
                bindings=frozenset({("call-b", tuple(B), "70000000-0000-4000-8000-000000000002")}),
            ),
        ),
        "activities": project(activities(), final_status="completed"),
        "approval": project(approvals(), final_status="interrupted"),
        "resumed": project(
            complete, run_id="30000000-0000-4000-8000-000000000002", final_status="completed"
        ),
        "cancelled": project(
            journal([("lifecycle", [], {"event": "running"})]), final_status="canceled"
        ),
        "earlyError": project(failed, final_status="failed"),
    }
    # Replay the *same* stored input. No model, graph, or tool is called again.
    assert passes["installed"] == project(installed, final_status="completed")
    return {"threadId": THREAD, "runId": PUBLIC_RUN, "streams": passes}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(cases()), ensure_ascii=False))
