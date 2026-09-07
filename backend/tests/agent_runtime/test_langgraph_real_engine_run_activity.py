from __future__ import annotations

import time

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime import langgraph_agent_stream_runner
from app.agent_runtime.e2e_scripted_model import (
    TOKEN_USAGE_MARKER,
    E2EScriptedChatModel,
)
from app.agent_runtime.run_metrics import RunMetricsAccumulator, RunMetricsSnapshot
from app.agent_runtime.runtime_config import AgentConfig

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)


async def _run_compiled_text_metrics(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> RunMetricsSnapshot:
    model = E2EScriptedChatModel(slow_stream_delay_seconds=0)
    monkeypatch.setattr(
        "app.agent_runtime.runtime_component_builder._build_model_candidates",
        lambda _cfg: [model],
    )
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", MemorySaver)
    metrics = RunMetricsAccumulator(started_at=time.monotonic())
    cfg = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="Return the deterministic response.",
        tools_config=[],
        thread_id="22222222-2222-4222-8222-222222222222",
    )

    _ = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            cfg,
            [{"role": "user", "content": prompt}],
            run_metrics=metrics,
            run_id="11111111-1111-4111-8111-111111111111",
        )
    ]
    return metrics.finalize("completed")


@pytest.mark.asyncio
async def test_real_compiled_default_text_run_has_timing_without_activity_or_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = await _run_compiled_text_metrics(
        monkeypatch,
        "durable run activity verification",
    )

    assert snapshot.generation_ms is not None
    assert snapshot.prompt_tokens is None
    assert snapshot.completion_tokens is None
    assert snapshot.activity == ()
    assert snapshot.root_tool_calls == 0
    assert snapshot.root_subagent_calls == 0
    assert snapshot.usage_complete is False


@pytest.mark.asyncio
async def test_real_compiled_usage_text_run_records_root_model_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = await _run_compiled_text_metrics(monkeypatch, TOKEN_USAGE_MARKER)
    payload = snapshot.to_persistence_payload()

    assert snapshot.generation_ms is not None
    assert snapshot.prompt_tokens == 30
    assert snapshot.completion_tokens == 12
    assert len(snapshot.activity) == 1
    assert snapshot.activity[0].kind == "model_usage"
    assert snapshot.activity[0].namespace == ()
    assert payload["activity"] == [snapshot.activity[0].to_persistence_payload()]
    assert payload["activity"][0]["kind"] == "model_usage"
    assert payload["activity"][0]["namespace"] == []
