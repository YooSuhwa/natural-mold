from __future__ import annotations

from uuid import UUID

import anyio
import pytest

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload
from tests.agent_runtime.test_run_metrics import FakeMonotonicClock


class ScriptedErrorCallbackAgent:
    """Scripted producer that exercises the configured LLM error callback."""

    def __init__(self, clock: FakeMonotonicClock, error: BaseException) -> None:
        self._clock = clock
        self._error = error

    async def astream_events(self, _input, *, config, **_kwargs):
        callback = next(
            item for item in config["callbacks"] if item.__class__.__name__ == "RunMetricsCallback"
        )
        run_id = UUID("00000000-0000-0000-0000-000000000005")
        callback.on_chat_model_start({}, [[]], run_id=run_id)

        async def stream():
            yield {
                "type": "event",
                "method": "messages",
                "params": {
                    "namespace": [],
                    "data": {
                        "id": "assistant-canceled",
                        "type": "ai",
                        "content": "partial",
                        "usage_metadata": {"input_tokens": 5, "output_tokens": 2},
                    },
                },
                "seq": 1,
                "event_id": "message-canceled",
            }
            self._clock.now = 4.0
            callback.on_llm_error(self._error, run_id=run_id)
            raise self._error

        return stream()


@pytest.mark.asyncio
async def test_stream_observes_original_usage_and_synthesized_tool_events() -> None:
    # Given: v3 values snapshots contain current model usage and a tool call.
    clock = FakeMonotonicClock(1.0)
    metrics = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    metrics.start_model_generation("model-1")
    agent = ProtocolAgent(
        [
            {
                "type": "event",
                "method": "values",
                "params": {
                    "namespace": [],
                    "data": {
                        "messages": [
                            {
                                "id": "assistant-1",
                                "type": "ai",
                                "content": "answer",
                                "usage_metadata": {
                                    "input_tokens": 7,
                                    "output_tokens": 3,
                                },
                                "tool_calls": [{"id": "tool-1", "name": "search", "args": {}}],
                            }
                        ]
                    },
                },
                "seq": 1,
                "event_id": "values-1",
            }
        ]
    )

    # When: the real protocol streaming adapter emits the run.
    _chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-metrics"}},
            run_id="run-metrics",
            run_metrics=metrics,
        )
    ]
    clock.now = 2.0
    metrics.finish_model_generation("model-1")
    snapshot = metrics.finalize("completed")

    # Then: current usage and the synthesized tool call reach one replay-safe collector.
    assert snapshot.prompt_tokens == 7
    assert snapshot.completion_tokens == 3
    assert snapshot.root_tool_calls == 1
    assert snapshot.root_subagent_calls == 0
    assert snapshot.descendant_subagent_calls == 0


@pytest.mark.asyncio
async def test_runner_callback_preserves_partial_metrics_when_producer_is_canceled(
    monkeypatch,
) -> None:
    # Given: a scripted LLM producer emits partial usage before cancellation.
    from app.agent_runtime import langgraph_agent_stream_runner
    from app.agent_runtime.runtime_config import AgentConfig

    clock = FakeMonotonicClock(1.0)
    metrics = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    canceled_type = anyio.get_cancelled_exc_class()
    agent = ScriptedErrorCallbackAgent(clock, canceled_type("scripted cancel"))
    cfg = AgentConfig(
        provider="fake",
        model_name="scripted",
        api_key=None,
        base_url=None,
        system_prompt="test",
        tools_config=[],
        thread_id="thread-cancel",
    )

    async def fake_prepare_agent(_cfg, *, messages_history, is_trigger_mode, run_id):
        return agent, [], {"configurable": {"thread_id": "thread-cancel"}}

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)

    # When: cancellation escapes the real runner/stream boundary.
    with pytest.raises(canceled_type):
        async for _chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            cfg,
            [],
            run_id="run-cancel",
            run_metrics=metrics,
        ):
            pass
    snapshot = metrics.finalize("canceled")

    # Then: partial usage survives and generation closes at the callback error boundary.
    assert snapshot.terminal_state == "canceled"
    assert snapshot.prompt_tokens == 5
    assert snapshot.completion_tokens == 2
    assert snapshot.generation_ms == 3_000.0


@pytest.mark.asyncio
async def test_runner_callback_preserves_partial_metrics_when_producer_fails(
    monkeypatch,
) -> None:
    # Given: a scripted LLM producer emits partial usage before a provider failure.
    from app.agent_runtime import langgraph_agent_stream_runner
    from app.agent_runtime.runtime_config import AgentConfig

    clock = FakeMonotonicClock(1.0)
    metrics = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    agent = ScriptedErrorCallbackAgent(clock, RuntimeError("scripted failure"))
    cfg = AgentConfig(
        provider="fake",
        model_name="scripted",
        api_key=None,
        base_url=None,
        system_prompt="test",
        tools_config=[],
        thread_id="thread-failure",
    )

    async def fake_prepare_agent(_cfg, *, messages_history, is_trigger_mode, run_id):
        return agent, [], {"configurable": {"thread_id": "thread-failure"}}

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)

    # When: the real stream boundary converts the provider exception to failure events.
    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            cfg,
            [],
            run_id="run-failure",
            run_metrics=metrics,
        )
    ]
    snapshot = metrics.finalize("failed")

    # Then: failure is emitted and the callback-closed partial metrics remain measured.
    assert any(sse_payload(chunk)["method"] == "error" for chunk in chunks)
    assert snapshot.terminal_state == "failed"
    assert snapshot.prompt_tokens == 5
    assert snapshot.completion_tokens == 2
    assert snapshot.generation_ms == 3_000.0


@pytest.mark.asyncio
async def test_empty_usage_message_does_not_claim_first_token_timing() -> None:
    # Given: a provider final usage message has no output content.
    agent = ProtocolAgent(
        [
            {
                "type": "event",
                "method": "messages",
                "params": {
                    "namespace": [],
                    "data": {
                        "id": "assistant-empty",
                        "type": "ai",
                        "content": "",
                        "usage_metadata": {"input_tokens": 5, "output_tokens": 2},
                    },
                },
                "seq": 1,
                "event_id": "message-empty",
            }
        ]
    )

    # When: usage is emitted through the real protocol stream.
    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-empty-token"}},
            run_id="run-empty-token",
        )
    ]
    usage_payload = next(
        payload["params"]["data"]["payload"]
        for payload in map(sse_payload, chunks)
        if payload["method"] == "custom" and payload["params"]["data"]["name"] == "usage"
    )

    # Then: generation elapsed is available, but TTFT/TPS are not fabricated.
    assert "generation_ms" in usage_payload
    assert "ttft_ms" not in usage_payload
    assert "tokens_per_second" not in usage_payload
