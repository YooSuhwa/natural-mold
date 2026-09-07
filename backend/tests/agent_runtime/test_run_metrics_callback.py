from __future__ import annotations

from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler, CallbackManager

from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_callback import configure_run_metrics_callback
from tests.agent_runtime.test_run_metrics import FakeMonotonicClock


class ExistingCallback(BaseCallbackHandler):
    pass


def test_callback_measures_each_model_interval_without_retaining_messages() -> None:
    # Given: a real callback lifecycle around two model calls separated by tool wait.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    config = configure_run_metrics_callback({}, accumulator)
    callback = config["callbacks"][0]
    first_run_id = UUID("00000000-0000-0000-0000-000000000001")
    second_run_id = UUID("00000000-0000-0000-0000-000000000002")

    # When: LangChain invokes callback boundaries with sensitive prompt content.
    callback.on_chat_model_start(
        {"name": "scripted-model"},
        [[{"role": "user", "content": "secret prompt must not be retained"}]],
        run_id=first_run_id,
    )
    clock.now = 2.0
    accumulator.observe(
        stored_protocol_event(
            run_id="run-1",
            thread_id="thread-1",
            seq=1,
            method="messages",
            data={
                "id": "assistant-1",
                "type": "ai",
                "content": "first token",
                "usage_metadata": {"input_tokens": 3, "output_tokens": 2},
            },
        )
    )
    clock.now = 3.0
    callback.on_llm_end(None, run_id=first_run_id)
    clock.now = 13.0
    callback.on_chat_model_start(
        {"name": "scripted-model"},
        [[{"role": "user", "content": "another secret"}]],
        run_id=second_run_id,
    )
    clock.now = 15.0
    callback.on_llm_error(RuntimeError("provider failed"), run_id=second_run_id)

    # Then: only actual model intervals are measured and prompt/error content is absent.
    assert accumulator.snapshot().generation_ms == 4_000.0
    assert "secret" not in repr(vars(callback))
    assert "provider failed" not in repr(vars(callback))


def test_callback_preserves_existing_callback_list_without_mutating_input() -> None:
    # Given: a runnable config already has a user callback list.
    existing = ExistingCallback()
    original_callbacks = [existing]
    accumulator = RunMetricsAccumulator(started_at=0.0)

    # When: run metrics instrumentation is configured.
    configured = configure_run_metrics_callback(
        {"callbacks": original_callbacks, "tags": ["existing"]},
        accumulator,
    )

    # Then: the original callback survives and caller-owned config/list stay untouched.
    assert configured is not None
    assert configured["callbacks"][0] is existing
    assert len(configured["callbacks"]) == 2
    assert original_callbacks == [existing]
    assert configured["tags"] == ["existing"]


def test_callback_copies_existing_callback_manager_before_adding_handler() -> None:
    # Given: a callback manager provider already owns one handler.
    existing = ExistingCallback()
    manager = CallbackManager([existing])

    # When: metrics instrumentation is attached.
    configured = configure_run_metrics_callback(
        {"callbacks": manager},
        RunMetricsAccumulator(started_at=0.0),
    )

    # Then: the provider is preserved in a copy and the original manager is unchanged.
    configured_manager = configured["callbacks"]
    assert configured_manager is not manager
    assert configured_manager.handlers[0] is existing
    assert len(configured_manager.handlers) == 2
    assert manager.handlers == [existing]


def test_callback_keeps_overlapping_model_invocations_distinct() -> None:
    # Given: root and subagent model calls overlap for one second.
    clock = FakeMonotonicClock(1.0)
    accumulator = RunMetricsAccumulator(started_at=0.0, monotonic=clock)
    callback = configure_run_metrics_callback({}, accumulator)["callbacks"][0]
    root_run_id = UUID("00000000-0000-0000-0000-000000000003")
    child_run_id = UUID("00000000-0000-0000-0000-000000000004")

    # When: both real lifecycle pairs are delivered by LangChain.
    callback.on_chat_model_start({}, [[]], run_id=root_run_id)
    clock.now = 2.0
    accumulator.observe(
        stored_protocol_event(
            run_id="run-1",
            thread_id="thread-1",
            seq=1,
            method="messages",
            data={
                "id": "assistant-1",
                "type": "ai",
                "content": "token",
                "usage_metadata": {"input_tokens": 4, "output_tokens": 2},
            },
        )
    )
    callback.on_chat_model_start({}, [[]], run_id=child_run_id)
    clock.now = 3.0
    callback.on_llm_end(None, run_id=root_run_id)
    clock.now = 4.0
    callback.on_llm_end(None, run_id=child_run_id)

    # Then: both provider intervals contribute, including their overlap.
    assert accumulator.snapshot().generation_ms == 4_000.0
