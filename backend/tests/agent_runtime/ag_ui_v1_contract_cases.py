"""Emit AG-UI SSE from persisted inputs for the cross-language stream contract."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from ag_ui.core import Event
from ag_ui.encoder import EventEncoder
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import TypeAdapter

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector, ChildVisibility
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from tests.agent_runtime.ag_ui_v1_approval_fixture import capture_approval_journals
from tests.agent_runtime.test_delegated_chat_replay import _current_journal
from tests.agent_runtime.test_moldy_v3_runtime_state import FakeToolBindingChatModel

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


async def successive_tool_turns() -> list[list[StoredProtocolEvent]]:
    """Two real turns on a single checkpoint; the provider reuses its tool-call ID."""
    executed: list[str] = []

    @tool
    def echo_turn(label: str) -> str:
        """Report the selected turn."""
        executed.append(label)
        return f"result-{label}"

    responses: list[BaseMessage] = []
    for label in ("first", "second"):
        responses.extend(
            [
                AIMessage(
                    id=f"issuer-{label}",
                    content="",
                    tool_calls=[{"id": "call_0", "name": "echo_turn", "args": {"label": label}}],
                ),
                AIMessage(id=f"answer-{label}", content=f"answer for {label}"),
            ]
        )
    graph = create_deep_agent(
        model=FakeToolBindingChatModel(responses=responses),
        tools=[echo_turn],
        checkpointer=InMemorySaver(),
    )
    turns: list[list[StoredProtocolEvent]] = []
    saved: list[StoredProtocolEvent] = []

    async def capture(records: list[dict[str, Any]]) -> None:
        saved.extend(cast(list[StoredProtocolEvent], records))

    for index, label in enumerate(("first", "second"), start=1):
        async for _ in stream_agent_response_langgraph(
            graph,
            {"messages": [{"id": f"user-{label}", "role": "user", "content": label}]},
            {"configurable": {"thread_id": THREAD}},
            run_id=f"40000000-0000-4000-8000-00000000001{index}",
            persist_callback=capture,
        ):
            pass
        turns.append(saved.copy())
        saved.clear()
    assert executed == ["first", "second"]
    return turns


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
    from app.agent_runtime.protocol_usage import collect_protocol_usage_event

    observed_usage, _ = collect_protocol_usage_event(
        records[-1],
        next_seq=len(records),
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )
    assert observed_usage is not None
    records.append(observed_usage)
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
        thread_id=records[0]["thread_id"],
        run_id=run_id,
        source_run_id=records[0]["run_id"],
        child_visibility=child_visibility,
    )
    return encode_journal(projector, records, final_status)


def encode_journal(
    projector: AgUiV1Projector, records: Sequence[StoredProtocolEvent], final_status: str
) -> str:
    encoder = EventEncoder()
    frames = [frame for record in records for frame in projector.project(record)]
    frames.extend(projector.finish(final_status))
    assert projector.closed
    decoded = PUBLIC_STREAM.validate_python([frame.model_dump(by_alias=True) for frame in frames])
    assert decoded == frames
    return "".join(encoder.encode(frame) for frame in frames)


async def checkpointed_approval(*, delegated: bool = False) -> tuple[str, str]:
    first, resumed = await capture_approval_journals(delegated=delegated)
    issuer = AgUiV1Projector(
        thread_id=THREAD, run_id="request-approval", source_run_id=first[0]["run_id"]
    )
    waiting = encode_journal(issuer, first, "interrupted")
    continuation = AgUiV1Projector(
        thread_id=THREAD,
        run_id="answer-approval",
        source_run_id=resumed[0]["run_id"],
        tool_continuations=issuer.unresolved_tool_calls,
    )
    return waiting, encode_journal(continuation, resumed, "completed")


async def cases() -> dict[str, object]:
    archived = [
        cast(StoredProtocolEvent, json.loads(row)) for row in ARCHIVE.read_text().splitlines()
    ]
    installed = cast(list[StoredProtocolEvent], await _current_journal())
    assert any(e["method"] == "tools" for e in installed)
    first, second = await successive_tool_turns()
    waiting, resumed = await checkpointed_approval()
    waiting_child, resumed_child = await checkpointed_approval(delegated=True)
    concurrent = siblings()
    complete = journal([("lifecycle", [], {"event": "completed"})])
    failed = journal([("error", [], {"message": "PRIVATE_ERROR_CANARY"})])
    passes = {
        "archived": project(archived, final_status="completed"),
        "installed": project(installed, final_status="completed"),
        "waitingTool": waiting,
        "resumedTool": resumed,
        "waitingChild": waiting_child,
        "resumedChild": resumed_child,
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
        "toolFirst": project(first, run_id="tool-first", final_status="completed"),
        "toolSecond": project(second, run_id="tool-second", final_status="completed"),
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
