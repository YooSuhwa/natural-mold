"""Level 2 integration — auto-compaction marker on the v3 stream.

Drives a *real* deepagents agent (with its auto-injected summarization
middleware) through ``stream_agent_response_langgraph`` and forces compaction by
giving the model a tiny ``max_input_tokens`` profile, then asserts the contract
from ``dev-plan-context-compaction-marker.md``:

* exactly one ``compaction(running)`` + one ``compaction(done, history_id)``,
* no internal offload path reaches wire, broker, persistence, or trace output,
* summarization tokens are suppressed (leak guard — they never reach the wire),
* ★ the real answer content survives intact (suppress must be exact-match only),
* with the flag off the legacy behavior holds (no markers, tokens flow through).

The window stays tiny (``max_input_tokens=50``) per §7 — no need to fill 85%.
"""

from __future__ import annotations

import itertools
import json
from typing import Any, assert_never

import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_streaming import (
    _compaction_history_id,
    _project_offload_egress_data,
    stream_agent_response_langgraph,
)
from app.agent_runtime.offload_storage import OffloadKind, logical_offload_id
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.runtime_component_builder import build_agent
from app.agent_runtime.runtime_policy import (
    JsonObject,
    ResolvedRuntimePolicy,
    resolve_runtime_policy,
)
from app.config import settings

_ANSWER = "ANSWER-CONTENT"
_FALLBACK_ANSWER = "FALLBACK-ANSWER-CONTENT"
_PRIMARY_UNAVAILABLE = "PRIMARY_UNAVAILABLE"


class _FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _FakeModel:
        return self


class _FallbackAfterCompactionModel(_FakeModel):
    """Fail a normal answer only after the primary has produced a summary."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = next(self.messages)
        match response:
            case AIMessage(content=content) if content == _PRIMARY_UNAVAILABLE:
                raise RuntimeError("primary model unavailable")
            case AIMessage():
                return ChatResult(generations=[ChatGeneration(message=response)])
            case str() as content:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
            case unreachable:
                assert_never(unreachable)


def _build_agent(summarization: JsonObject | None = None) -> Any:
    fake = _FakeModel(messages=itertools.cycle([AIMessage(content=_ANSWER)]))
    # Tiny window → deepagents summarizes on the next turn.
    fake.profile = {"max_input_tokens": 50}
    if summarization is not None:
        policy: JsonObject = {
            "version": 1,
            "filesystem": {"mode": "artifact_write"},
            "todo": {"enabled": True},
            "summarization": summarization,
        }
        runtime_policy = resolve_runtime_policy(policy)
        return build_agent(
            fake,
            [],
            "sys",
            checkpointer=InMemorySaver(),
            runtime_policy=runtime_policy,
        )
    return create_deep_agent(
        model=fake, tools=[], system_prompt="sys", checkpointer=InMemorySaver()
    )


def _stored_auto_policy() -> ResolvedRuntimePolicy:
    policy: JsonObject = {
        "version": 1,
        "filesystem": {"mode": "artifact_write"},
        "todo": {"enabled": True},
        "summarization": {"mode": "auto"},
    }
    return resolve_runtime_policy(policy)


def _parse_sse(raw: str) -> dict[str, Any]:
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    return {}


async def _drive(
    agent: Any,
    thread_id: str,
    run_id: str,
    content: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    config = {"configurable": {"thread_id": thread_id}}
    events: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    broker = EventBroker(run_id)

    async def persist(batch: list[dict[str, Any]]) -> None:
        persisted.extend(batch)

    async for sse in stream_agent_response_langgraph(
        agent,
        [HumanMessage(content=content)],
        config,
        run_id=run_id,
        broker=broker,
        persist_callback=persist,
        trace_sink=trace,
    ):
        events.append(_parse_sse(sse))
    broker_events = [event["data"] async for event in broker.subscribe()]
    return events, persisted, trace, broker_events


def _compaction_payloads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.get("method") != "custom":
            continue
        data = event.get("params", {}).get("data")
        if isinstance(data, dict) and data.get("name") == "moldy.compaction":
            payload = data.get("payload")
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def _summary_token_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    for event in events:
        if event.get("method") != "messages":
            continue
        data = event.get("params", {}).get("data")
        if not isinstance(data, dict):
            continue
        metadata = data.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("lc_source") == "summarization":
            leaks.append(event)
    return leaks


def _answer_text(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("method") != "messages":
            continue
        data = event.get("params", {}).get("data")
        if not isinstance(data, dict):
            continue
        delta = data.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            parts.append(delta["text"])
        content = data.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            parts.append(content["text"])
    return "".join(parts)


def _serialized(events: Any) -> str:
    return json.dumps(events, ensure_ascii=False, sort_keys=True)


def test_compaction_projection_strips_paths_without_mutating_graph_state() -> None:
    history_path = "/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    spill_path = "/.moldy-offload/scope/large_tool_results/tool-call"
    raw_state = {
        "_summarization_event": {"cutoff_index": 9, "file_path": history_path},
        "messages": [{"content": f"Stored summary at {history_path}"}],
        "files": {
            history_path: {"content": f"Summary source: {history_path}"},
            spill_path: {"content": "large result"},
        },
    }

    projected = _project_offload_egress_data(raw_state)

    assert raw_state["_summarization_event"]["file_path"] == history_path
    assert history_path in raw_state["files"]
    serialized = _serialized(projected)
    assert history_path not in serialized
    assert spill_path not in serialized
    assert '"file_path"' not in serialized
    history_id = projected["_summarization_event"]["history_id"]
    assert history_id.startswith("history_")
    assert len(history_id) == len("history_") + 24
    assert history_id == logical_offload_id(OffloadKind.HISTORY, history_path)


def test_compaction_projection_preserves_nested_messages_usage_and_artifact_metadata() -> None:
    """Offload redaction must not discard ordinary projected stream state."""

    history_path = "/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    raw_state = {
        "_summarization_event": {"cutoff_index": 9, "file_path": history_path},
        "messages": [[{"content": [{"type": "text", "text": "visible nested reply"}]}]],
        "usage": {"input_tokens": 42, "output_tokens": 7},
        "artifacts": [{"name": "summary.md", "path": history_path}],
    }

    projected = _project_offload_egress_data(raw_state)

    assert projected["messages"] == raw_state["messages"]
    assert projected["usage"] == raw_state["usage"]
    assert projected["artifacts"] == [
        {
            "name": "summary.md",
            "path": logical_offload_id(OffloadKind.HISTORY, history_path),
        }
    ]
    assert history_path not in _serialized(projected)


def test_compaction_projection_fails_closed_for_untrusted_physical_paths() -> None:
    absolute_spill = "/tmp/data/.moldy-internal/offload/spill/owner/run/actor/leaf"
    relative_history = ".moldy-internal/offload/history/owner/conversation/session"

    projected = _project_offload_egress_data(
        {"absolute": absolute_spill, "relative": relative_history}
    )

    serialized = _serialized(projected)
    assert absolute_spill not in serialized
    assert relative_history not in serialized
    assert projected["absolute"] == "internal_reference_redacted"
    assert projected["relative"] == "internal_reference_redacted"


def test_committed_compaction_with_untrusted_file_path_emits_no_history_id() -> None:
    secret = "COMPACTION_SECRET"
    raw_path = f"/unknown/.moldy-internal/offload/history/a/b/c/{secret}.md"
    event = stored_protocol_event(
        run_id="run-untrusted",
        thread_id="thread-untrusted",
        seq=1,
        method="values",
        data={"_summarization_event": {"file_path": raw_path, "cutoff_index": 2}},
    )

    projected = _project_offload_egress_data(event["data"])

    assert _compaction_history_id(event) is None
    assert projected == {
        "_summarization_event": {"cutoff_index": 2, "internal_reference_redacted": True}
    }
    serialized = _serialized(projected)
    assert raw_path not in serialized
    assert secret not in serialized
    assert "history_" not in serialized


def test_compaction_projection_uses_session_identity_not_conversation_only() -> None:
    first_path = "/conversation_history/session_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md"
    second_path = "/conversation_history/session_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md"
    first = _project_offload_egress_data({"_summarization_event": {"file_path": first_path}})
    second = _project_offload_egress_data({"_summarization_event": {"file_path": second_path}})

    assert (
        first["_summarization_event"]["history_id"] != second["_summarization_event"]["history_id"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summarization",
    [
        {"mode": "auto"},
        {"mode": "preset", "preset": "balanced_context_v1"},
    ],
    ids=["auto", "balanced-context"],
)
async def test_stored_summarization_projects_two_runs_without_offload_path_leaks(
    summarization: JsonObject,
) -> None:
    """Each stored policy keeps repeated compaction's public stream contract."""

    agent = _build_agent(summarization)
    thread_id = "thread-compact"
    await _drive(agent, thread_id, "run-before-compaction", "첫 질문 " * 40)
    first_events, first_persisted, first_trace, first_broker = await _drive(
        agent, thread_id, "run-first-compaction", "둘째 질문 " * 40
    )
    second_events, second_persisted, second_trace, second_broker = await _drive(
        agent, thread_id, "run-second-compaction", "셋째 질문 " * 40
    )

    first_payloads = _compaction_payloads(first_events)
    second_payloads = _compaction_payloads(second_events)
    assert [payload.get("state") for payload in first_payloads].count("running") == 1
    assert [payload.get("state") for payload in first_payloads].count("done") == 1
    assert [payload.get("state") for payload in second_payloads].count("running") == 1
    assert [payload.get("state") for payload in second_payloads].count("done") == 1

    first_done = next(payload for payload in first_payloads if payload.get("state") == "done")
    second_done = next(payload for payload in second_payloads if payload.get("state") == "done")
    history_id = first_done.get("history_id")
    assert isinstance(history_id, str)
    assert history_id.startswith("history_")
    assert second_done.get("history_id") == history_id
    assert "offload_path" not in first_done
    assert "offload_path" not in second_done
    assert isinstance(first_done.get("cutoff_index"), int)
    assert first_done["cutoff_index"] > 0

    # Leak guard — summarization tokens never reach the wire.
    assert _summary_token_events(first_events) == []
    assert _summary_token_events(second_events) == []
    # ★ Regression guard — the real answer content must survive the suppress.
    assert _ANSWER in _answer_text(first_events)
    assert _ANSWER in _answer_text(second_events)

    for projection in (
        first_events,
        first_persisted,
        first_trace,
        first_broker,
        second_events,
        second_persisted,
        second_trace,
        second_broker,
    ):
        serialized = _serialized(projection)
        assert "offload_path" not in serialized
        assert "file_path" not in serialized
        assert "/conversation_history/" not in serialized
        assert "/.moldy-offload/" not in serialized


@pytest.mark.asyncio
async def test_stored_auto_compaction_keeps_normal_answer_fallback_functional() -> None:
    """A primary summary must not disable fallback for the following user-facing answer."""

    primary = _FallbackAfterCompactionModel(
        messages=iter(
            [
                AIMessage(content=_ANSWER),
                AIMessage(content="summary state"),
                AIMessage(content=_PRIMARY_UNAVAILABLE),
            ]
        )
    )
    primary.profile = {"max_input_tokens": 50}
    fallback = _FakeModel(messages=itertools.repeat(AIMessage(content=_FALLBACK_ANSWER)))
    agent = build_agent(
        primary,
        [],
        "sys",
        middleware=[ModelFallbackMiddleware(fallback)],
        checkpointer=InMemorySaver(),
        runtime_policy=_stored_auto_policy(),
    )

    await _drive(agent, "thread-fallback", "run-before-compaction", "첫 질문 " * 40)
    events, persisted, trace, broker = await _drive(
        agent, "thread-fallback", "run-after-compaction", "둘째 질문 " * 40
    )

    payloads = _compaction_payloads(events)
    assert [payload.get("state") for payload in payloads].count("done") == 1
    assert _FALLBACK_ANSWER in _answer_text(events)
    for projection in (events, persisted, trace, broker):
        assert "/conversation_history/" not in _serialized(projection)


@pytest.mark.asyncio
async def test_compaction_disabled_flows_summary_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "compaction_marker_enabled", False)
    agent = _build_agent()
    thread_id = "thread-off"
    await _drive(agent, thread_id, "run-off-before", "첫 질문 " * 40)
    events, _, _, _ = await _drive(agent, thread_id, "run-off", "둘째 질문 " * 40)

    # Flag off → no markers, and the legacy v3 path leaves summarization tokens
    # in the stream (no suppress).
    assert _compaction_payloads(events) == []
    assert len(_summary_token_events(events)) > 0
    assert _ANSWER in _answer_text(events)
