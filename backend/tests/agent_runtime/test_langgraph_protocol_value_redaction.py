"""ADR-021 — value-based trace redaction on the REAL protocol path.

The 33 pre-existing unit tests only exercised the pure functions
(``redact_protocol_data`` with an explicit ``secret_values``, the ContextVar
plumbing in isolation). They missed the actual chat path: the LangGraph
protocol runner (``execute_agent_stream_langgraph`` →
``_run_langgraph_agent_stream``) never installed the run-scoped secret
ContextVar, so ``redact_protocol_data`` (called from ``langgraph_streaming``'s
``emit``) saw ``None`` and value-based masking was a permanent no-op there.

These tests drive the *real* ``stream_agent_response_langgraph`` (only
``_prepare_agent`` is stubbed) so the run wiring — set_run_secrets BEFORE
prepare, ContextVar surviving across stream yields into the emit/persist
closures — is what is under test. They FAIL against the unwired runner and
PASS once C1 installs the ContextVar.

The secret used (``Xq9fGh2KpL8mNvR4tokenvalue``) is deliberately opaque: it has
no recognised key in front of it and matches none of the fallback value
heuristics (not ``Bearer``/``sk-``/JWT/cookie/DSN). The ONLY way it gets
masked is value-based replacement of the run's actual injected secret — so a
green test proves value-based masking, not a heuristic accidentally catching
the shape.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent_runtime import langgraph_agent_stream_runner
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.runtime_config import AgentConfig
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload

# Opaque, high-entropy token: no sensitive key prefix, not Bearer/sk-/JWT/DSN,
# so heuristics cannot mask it — only value-based replacement can.
OPAQUE_SECRET = "Xq9fGh2KpL8mNvR4tokenvalue"


def _cfg_with_tool_secret() -> AgentConfig:
    """A chat-shaped cfg whose tool credential is the opaque secret.

    Mirrors what ``conversation_stream_service`` produces: the plaintext tool
    credential ends up both in ``tools_config`` and in the eager
    ``secret_values`` set the runner installs into the ContextVar.
    """

    return AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="You are helpful.",
        tools_config=[{"definition_key": "some_tool", "credentials": {"api_key": OPAQUE_SECRET}}],
        thread_id="thread-redact",
        secret_values={OPAQUE_SECRET},
    )


def _tool_output_event(content: str) -> dict[str, Any]:
    """A protocol ``messages`` event whose ToolMessage echoes the secret.

    This is the realistic leak: a tool was called with an interpolated
    credential and the result text (or the model's echo of it) contains the
    raw value, which would otherwise be persisted / streamed verbatim.
    """

    return {
        "type": "event",
        "method": "messages",
        "params": {
            "namespace": [],
            "data": {
                "id": "tool-msg-1",
                "type": "ToolMessage",
                "content": content,
                "name": "some_tool",
            },
        },
        "seq": 1,
        "event_id": "tool-upstream-1",
    }


async def _fake_prepare_agent(agent: ProtocolAgent):
    async def _prepare(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
        return agent, ["lc-message"], {"configurable": {"thread_id": "thread-redact"}}

    return _prepare


@pytest.mark.asyncio
async def test_protocol_stream_masks_opaque_tool_secret_in_sse_and_persist(monkeypatch) -> None:
    # Plain prose around the secret: NO sensitive key (``token=``/``api_key=``)
    # in front of it, so SENSITIVE_ASSIGNMENT_RE / the value heuristics cannot
    # touch it. Only value-based replacement of the run secret masks it.
    leaky = f"the search returned {OPAQUE_SECRET} as the result"
    agent = ProtocolAgent([_tool_output_event(leaky)])

    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "_prepare_agent",
        await _fake_prepare_agent(agent),
    )
    # IMPORTANT: do NOT stub stream_agent_response_langgraph — we want the real
    # emit() → redact_protocol_data() path under the runner's ContextVar.

    trace_sink: list[dict[str, Any]] = []
    persisted: list[list[dict[str, Any]]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted.append(events)

    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            _cfg_with_tool_secret(),
            [{"role": "user", "content": "use the tool"}],
            trace_sink=trace_sink,
            persist_callback=persist,
            run_id="run-redact",
        )
    ]

    # SSE wire output: the opaque secret must be gone, replaced by <redacted>.
    sse_blob = "".join(chunks)
    assert OPAQUE_SECRET not in sse_blob, "opaque tool secret leaked into SSE egress"
    messages_payloads = [
        sse_payload(chunk) for chunk in chunks if sse_payload(chunk)["method"] == "messages"
    ]
    assert messages_payloads, "expected at least one messages SSE frame"
    masked_content = messages_payloads[0]["params"]["data"]["content"]
    assert OPAQUE_SECRET not in masked_content
    assert "<redacted>" in masked_content

    # Persistence buffer (message_events): also masked.
    persist_blob = json.dumps(persisted)
    assert OPAQUE_SECRET not in persist_blob, "opaque tool secret leaked into persistence"
    assert "<redacted>" in persist_blob

    # trace_sink feeds the operator debug trace; must be masked too.
    assert OPAQUE_SECRET not in json.dumps(trace_sink)


@pytest.mark.asyncio
async def test_protocol_stream_contextvar_resets_after_run(monkeypatch) -> None:
    """The runner must reset the ContextVar so secrets never leak across runs.

    Run 1 injects the secret; run 2 (different cfg, no secret) must NOT mask a
    string that happens to equal run 1's secret — proving ``reset_run_secrets``
    fired in the ``finally``.
    """

    from app.agent_runtime.run_secrets import get_run_secrets

    agent1 = ProtocolAgent(
        [_tool_output_event(f"the search returned {OPAQUE_SECRET} as the result")]
    )
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "_prepare_agent",
        await _fake_prepare_agent(agent1),
    )
    _ = [
        c
        async for c in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            _cfg_with_tool_secret(),
            [{"role": "user", "content": "go"}],
            run_id="run-1",
        )
    ]

    # After the generator is fully consumed the ContextVar is back to unset.
    assert get_run_secrets() is None

    # Run 2: a cfg WITHOUT the secret. The same opaque string in the output is
    # not in run 2's set, so it survives (heuristics can't catch this shape).
    agent2 = ProtocolAgent(
        [_tool_output_event(f"the search returned {OPAQUE_SECRET} as the result")]
    )
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "_prepare_agent",
        await _fake_prepare_agent(agent2),
    )
    cfg2 = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="You are helpful.",
        tools_config=[],
        thread_id="thread-redact-2",
        secret_values=set(),
    )
    chunks2 = [
        c
        async for c in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            cfg2,
            [{"role": "user", "content": "go"}],
            run_id="run-2",
        )
    ]
    assert OPAQUE_SECRET in "".join(chunks2), (
        "run-2 has no such secret; the opaque value must NOT be masked "
        "(else the ContextVar leaked across runs)"
    )


@pytest.mark.asyncio
async def test_raw_stream_without_contextvar_does_not_mask(monkeypatch) -> None:
    """Baseline that pins WHY C1 is needed.

    Calling ``stream_agent_response_langgraph`` directly (no runner, so no
    ``set_run_secrets``) leaves the ContextVar unset; the opaque secret is NOT
    masked. This is exactly the pre-fix behaviour of the protocol chat path —
    the runner wiring (C1) is what makes the difference.
    """

    from app.agent_runtime.run_secrets import get_run_secrets

    assert get_run_secrets() is None
    agent = ProtocolAgent(
        [_tool_output_event(f"the search returned {OPAQUE_SECRET} as the result")]
    )
    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-raw"}},
            run_id="run-raw",
        )
    ]
    assert OPAQUE_SECRET in "".join(chunks)
