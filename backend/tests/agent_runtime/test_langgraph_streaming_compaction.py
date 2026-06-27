"""Level 2 integration — auto-compaction marker on the v3 stream.

Drives a *real* deepagents agent (with its auto-injected summarization
middleware) through ``stream_agent_response_langgraph`` and forces compaction by
giving the model a tiny ``max_input_tokens`` profile, then asserts the contract
from ``dev-plan-context-compaction-marker.md``:

* exactly one ``compaction(running)`` + one ``compaction(done, offload_path)``,
* summarization tokens are suppressed (leak guard — they never reach the wire),
* ★ the real answer content survives intact (suppress must be exact-match only),
* with the flag off the legacy behavior holds (no markers, tokens flow through).

The window stays tiny (``max_input_tokens=50``) per §7 — no need to fill 85%.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.config import settings

_ANSWER = "ANSWER-CONTENT"


class _FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _FakeModel:
        return self


def _build_agent() -> Any:
    fake = _FakeModel(messages=itertools.cycle([AIMessage(content=_ANSWER)]))
    # Tiny window → deepagents summarizes on the next turn.
    fake.profile = {"max_input_tokens": 50}
    return create_deep_agent(
        model=fake, tools=[], system_prompt="sys", checkpointer=InMemorySaver()
    )


def _parse_sse(raw: str) -> dict[str, Any]:
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    return {}


async def _drive(agent: Any, thread_id: str, content: str) -> list[dict[str, Any]]:
    config = {"configurable": {"thread_id": thread_id}}
    events: list[dict[str, Any]] = []
    async for sse in stream_agent_response_langgraph(
        agent, [HumanMessage(content=content)], config, run_id=f"run-{thread_id}"
    ):
        events.append(_parse_sse(sse))
    return events


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


@pytest.mark.asyncio
async def test_compaction_emits_running_done_and_suppresses_summary_tokens() -> None:
    agent = _build_agent()
    thread_id = "thread-compact"
    await _drive(agent, thread_id, "첫 질문 " * 40)  # accumulate history
    events = await _drive(agent, thread_id, "둘째 질문 " * 40)  # triggers compaction

    payloads = _compaction_payloads(events)
    states = [payload.get("state") for payload in payloads]
    assert states.count("running") == 1, payloads
    assert states.count("done") == 1, payloads

    done = next(payload for payload in payloads if payload.get("state") == "done")
    assert done.get("offload_path") == f"/conversation_history/{thread_id}.md"
    assert isinstance(done.get("cutoff_index"), int)
    assert done["cutoff_index"] > 0

    # Leak guard — summarization tokens never reach the wire.
    assert _summary_token_events(events) == []
    # ★ Regression guard — the real answer content must survive the suppress.
    assert _ANSWER in _answer_text(events)


@pytest.mark.asyncio
async def test_compaction_disabled_flows_summary_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "compaction_marker_enabled", False)
    agent = _build_agent()
    thread_id = "thread-off"
    await _drive(agent, thread_id, "첫 질문 " * 40)
    events = await _drive(agent, thread_id, "둘째 질문 " * 40)

    # Flag off → no markers, and the legacy v3 path leaves summarization tokens
    # in the stream (no suppress).
    assert _compaction_payloads(events) == []
    assert len(_summary_token_events(events)) > 0
    assert _ANSWER in _answer_text(events)
