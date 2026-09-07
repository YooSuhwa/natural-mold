from __future__ import annotations

from typing import Any

import pytest

from app.agent_runtime.runtime_config import AgentConfig


def _cfg() -> AgentConfig:
    return AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="You are helpful.",
        tools_config=[],
        thread_id="thread-runner",
    )


@pytest.mark.asyncio
async def test_execute_agent_stream_langgraph_accepts_worker_common_kwargs(
    monkeypatch,
) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, Any] = {}
    msg_id_sink: list[str] = []
    trace_sink: list[dict[str, Any]] = []
    error_sink = []
    langfuse_sink = []
    broker = object()
    artifact_recorder = object()

    async def persist_callback(_events) -> None:
        captured["persisted"] = True

    async def fake_prepare_agent(
        _cfg: AgentConfig, *, messages_history, is_trigger_mode=False, run_id
    ):
        captured["messages_history"] = messages_history
        return "agent", ["lc-message"], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(_agent, _input, _config, **kwargs):
        captured["kwargs"] = kwargs
        yield "protocol-chunk"

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "stream_agent_response_langgraph",
        fake_stream,
    )

    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            _cfg(),
            [{"role": "user", "content": "hello"}],
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id="run-worker",
            artifact_recorder=artifact_recorder,
            moldy_source="chat",
            langfuse_sink=langfuse_sink,
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert msg_id_sink == ["run-worker"]
    assert captured["messages_history"] == [{"role": "user", "content": "hello"}]
    assert captured["kwargs"]["trace_sink"] is trace_sink
    assert captured["kwargs"]["msg_id_sink"] is msg_id_sink
    assert captured["kwargs"]["error_sink"] is error_sink
    assert captured["kwargs"]["broker"] is broker
    assert captured["kwargs"]["persist_callback"] is persist_callback
    assert captured["kwargs"]["run_id"] == "run-worker"
    assert captured["kwargs"]["artifact_recorder"] is artifact_recorder


@pytest.mark.asyncio
async def test_resume_agent_stream_langgraph_accepts_worker_common_kwargs(monkeypatch) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, Any] = {}
    msg_id_sink: list[str] = []
    trace_sink: list[dict[str, Any]] = []
    error_sink = []
    langfuse_sink = []
    broker = object()
    artifact_recorder = object()

    async def persist_callback(_events) -> None:
        captured["persisted"] = True

    async def fake_prepare_agent(
        _cfg: AgentConfig, *, messages_history, is_trigger_mode=False, run_id
    ):
        captured["messages_history"] = messages_history
        return "agent", [], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(_agent, input_, _config, **kwargs):
        captured["input"] = input_
        captured["kwargs"] = kwargs
        yield "protocol-chunk"

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "stream_agent_response_langgraph",
        fake_stream,
    )

    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.resume_agent_stream_langgraph(
            _cfg(),
            {"decisions": [{"type": "approve"}]},
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id="run-resume",
            artifact_recorder=artifact_recorder,
            moldy_source="resume",
            langfuse_sink=langfuse_sink,
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert msg_id_sink == ["run-resume"]
    assert captured["messages_history"] == []
    assert captured["input"].resume == {"decisions": [{"type": "approve"}]}
    assert captured["kwargs"]["trace_sink"] is trace_sink
    assert captured["kwargs"]["msg_id_sink"] is msg_id_sink
    assert captured["kwargs"]["error_sink"] is error_sink
    assert captured["kwargs"]["broker"] is broker
    assert captured["kwargs"]["persist_callback"] is persist_callback
    assert captured["kwargs"]["run_id"] == "run-resume"
    assert captured["kwargs"]["artifact_recorder"] is artifact_recorder
