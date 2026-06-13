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
