from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agent_runtime.runtime_config import AgentConfig
from app.hooks import HookResult


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


def _cfg_with_hooks() -> AgentConfig:
    return AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="You are helpful.",
        tools_config=[],
        thread_id="thread-runner",
        user_id=str(uuid.uuid4()),
        cost_per_input_token=0.01,
        cost_per_output_token=0.02,
    )


@pytest.mark.asyncio
async def test_execute_agent_stream_langgraph_posts_usage_to_hooks(monkeypatch) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    posted: list[HookResult] = []

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
        return "agent", ["lc-message"], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(_agent, _input, _config, **kwargs):
        kwargs["usage_sink"].update(
            {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "estimated_cost": 0.22,
            }
        )
        yield "protocol-chunk"

    async def fake_run_pre(_ctx) -> None:
        return None

    async def fake_run_post(_ctx, result: HookResult) -> None:
        posted.append(result)

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "stream_agent_response_langgraph",
        fake_stream,
    )
    monkeypatch.setattr(langgraph_agent_stream_runner.hooks, "run_pre", fake_run_pre)
    monkeypatch.setattr(langgraph_agent_stream_runner.hooks, "run_post", fake_run_post)

    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            _cfg_with_hooks(),
            [{"role": "user", "content": "hello"}],
            run_id="run-usage",
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert posted[0].tokens_in == 12
    assert posted[0].tokens_out == 5
    assert posted[0].cost_usd == 0.22


@pytest.mark.asyncio
async def test_execute_agent_stream_langgraph_uses_prepared_messages(monkeypatch) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, Any] = {}

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
        captured["messages_history"] = messages_history
        return "agent", ["lc-message"], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(agent, input_, config, **kwargs):
        captured["agent"] = agent
        captured["input"] = input_
        captured["config"] = config
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
            run_id="run-1",
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert captured["messages_history"] == [{"role": "user", "content": "hello"}]
    assert captured["input"] == ["lc-message"]
    assert captured["kwargs"]["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_execute_agent_stream_langgraph_passes_state_dict_inputs(monkeypatch) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, Any] = {}

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
        captured["messages_history"] = messages_history
        return "agent", [], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(_agent, input_, _config, **_kwargs):
        captured["input"] = input_
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
            {"messages": []},
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert captured["messages_history"] == []
    assert captured["input"] == {"messages": []}


@pytest.mark.asyncio
async def test_execute_agent_stream_langgraph_passes_artifact_recorder(monkeypatch) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, Any] = {}
    recorder = object()

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
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
            artifact_recorder=recorder,
            run_id="run-artifacts",
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert captured["kwargs"]["artifact_recorder"] is recorder


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

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
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
    assert captured["kwargs"]["error_sink"] is error_sink
    assert captured["kwargs"]["broker"] is broker
    assert captured["kwargs"]["persist_callback"] is persist_callback
    assert captured["kwargs"]["run_id"] == "run-worker"
    assert captured["kwargs"]["artifact_recorder"] is artifact_recorder


@pytest.mark.asyncio
async def test_resume_agent_stream_langgraph_passes_command_resume(monkeypatch) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured = {}

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
        captured["messages_history"] = messages_history
        return "agent", [], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(_agent, input_, _config, **_kwargs):
        captured["input"] = input_
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
        )
    ]

    assert chunks == ["protocol-chunk"]
    assert captured["messages_history"] == []
    assert captured["input"].resume == {"decisions": [{"type": "approve"}]}


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

    async def fake_prepare_agent(_cfg: AgentConfig, *, messages_history, is_trigger_mode=False):
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
    assert captured["kwargs"]["error_sink"] is error_sink
    assert captured["kwargs"]["broker"] is broker
    assert captured["kwargs"]["persist_callback"] is persist_callback
    assert captured["kwargs"]["run_id"] == "run-resume"
    assert captured["kwargs"]["artifact_recorder"] is artifact_recorder
