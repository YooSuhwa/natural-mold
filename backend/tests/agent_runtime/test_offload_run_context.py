from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from uuid import UUID

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
        thread_id="conversation-offload-context",
    )


class _FakeLangfuseContext:
    trace = None

    def configure_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return config

    def activate(self, **_kwargs: Any):
        return nullcontext()

    def flush(self) -> None:
        return None


def _assert_effective_run_id(
    captured: dict[str, str],
    requested_run_id: str | None,
) -> None:
    effective_run_id = captured["prepared"]
    assert captured["streamed"] == effective_run_id
    assert captured["langfuse"] == effective_run_id
    if requested_run_id is None:
        assert UUID(effective_run_id).version == 4
    else:
        assert effective_run_id == requested_run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_run_id", [None, "caller-legacy-stream"])
async def test_legacy_stream_propagates_effective_run_id_before_prepare(
    monkeypatch: pytest.MonkeyPatch,
    requested_run_id: str | None,
) -> None:
    from app.agent_runtime import agent_stream_runner

    captured: dict[str, str] = {}

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool,
        run_id: str,
    ) -> tuple[str, list[str], dict[str, Any]]:
        captured["prepared"] = run_id
        assert is_trigger_mode is False
        return "agent", ["prepared-message"], {"configurable": {}}

    async def fake_stream(_agent: str, _input: Any, _config: dict[str, Any], **kwargs: Any):
        captured["streamed"] = kwargs["run_id"]
        yield "streamed"

    def fake_langfuse(_cfg: AgentConfig, *, run_id: str, source: str) -> _FakeLangfuseContext:
        captured["langfuse"] = run_id
        assert source == "chat"
        return _FakeLangfuseContext()

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(agent_stream_runner, "stream_agent_response", fake_stream)
    monkeypatch.setattr(agent_stream_runner, "build_langfuse_run_context", fake_langfuse)

    chunks = [
        chunk
        async for chunk in agent_stream_runner.execute_agent_stream(
            _cfg(),
            [{"role": "user", "content": "hello"}],
            run_id=requested_run_id,
        )
    ]

    assert chunks == ["streamed"]
    _assert_effective_run_id(captured, requested_run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_run_id", [None, "caller-legacy-resume"])
async def test_legacy_resume_propagates_effective_run_id_before_prepare(
    monkeypatch: pytest.MonkeyPatch,
    requested_run_id: str | None,
) -> None:
    from app.agent_runtime import agent_stream_runner

    captured: dict[str, str] = {}

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool,
        run_id: str,
    ) -> tuple[str, list[str], dict[str, Any]]:
        captured["prepared"] = run_id
        assert messages_history == []
        assert is_trigger_mode is False
        return "agent", [], {"configurable": {}}

    async def fake_stream(_agent: str, _input: Any, _config: dict[str, Any], **kwargs: Any):
        captured["streamed"] = kwargs["run_id"]
        yield "resumed"

    def fake_langfuse(_cfg: AgentConfig, *, run_id: str, source: str) -> _FakeLangfuseContext:
        captured["langfuse"] = run_id
        assert source == "resume"
        return _FakeLangfuseContext()

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(agent_stream_runner, "stream_agent_response", fake_stream)
    monkeypatch.setattr(agent_stream_runner, "build_langfuse_run_context", fake_langfuse)

    chunks = [
        chunk
        async for chunk in agent_stream_runner.resume_agent_stream(
            _cfg(),
            {"approved": True},
            run_id=requested_run_id,
        )
    ]

    assert chunks == ["resumed"]
    _assert_effective_run_id(captured, requested_run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_run_id", [None, "caller-trigger-invoke"])
async def test_trigger_invoke_propagates_effective_run_id_before_prepare(
    monkeypatch: pytest.MonkeyPatch,
    requested_run_id: str | None,
) -> None:
    from app.agent_runtime import agent_stream_runner

    captured: dict[str, str] = {}

    class FakeAgent:
        async def ainvoke(
            self,
            _input: dict[str, list[str]],
            *,
            config: dict[str, Any],
        ) -> dict[str, list[Any]]:
            captured["streamed"] = captured["prepared"]
            assert config == {"configurable": {}}
            return {"messages": [SimpleNamespace(content="completed")]}

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool,
        run_id: str,
    ) -> tuple[FakeAgent, list[str], dict[str, Any]]:
        captured["prepared"] = run_id
        assert is_trigger_mode is True
        return FakeAgent(), ["prepared-message"], {"configurable": {}}

    def fake_langfuse(_cfg: AgentConfig, *, run_id: str, source: str) -> _FakeLangfuseContext:
        captured["langfuse"] = run_id
        assert source == "trigger"
        return _FakeLangfuseContext()

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(agent_stream_runner, "build_langfuse_run_context", fake_langfuse)

    result = await agent_stream_runner.execute_agent_invoke(
        _cfg(),
        [{"role": "user", "content": "hello"}],
        run_id=requested_run_id,
    )

    assert result == "completed"
    _assert_effective_run_id(captured, requested_run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_run_id", [None, "caller-langgraph-stream"])
async def test_langgraph_stream_propagates_effective_run_id_before_prepare(
    monkeypatch: pytest.MonkeyPatch,
    requested_run_id: str | None,
) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, str] = {}

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool,
        run_id: str,
    ) -> tuple[str, list[str], dict[str, Any]]:
        captured["prepared"] = run_id
        assert is_trigger_mode is False
        return "agent", ["prepared-message"], {"configurable": {}}

    async def fake_stream(_agent: str, _input: Any, _config: dict[str, Any], **kwargs: Any):
        captured["streamed"] = kwargs["run_id"]
        yield "streamed"

    def fake_langfuse(_cfg: AgentConfig, *, run_id: str, source: str) -> _FakeLangfuseContext:
        captured["langfuse"] = run_id
        assert source == "chat"
        return _FakeLangfuseContext()

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "stream_agent_response_langgraph",
        fake_stream,
    )
    monkeypatch.setattr(langgraph_agent_stream_runner, "build_langfuse_run_context", fake_langfuse)

    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            _cfg(),
            [{"role": "user", "content": "hello"}],
            run_id=requested_run_id,
        )
    ]

    assert chunks == ["streamed"]
    _assert_effective_run_id(captured, requested_run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_run_id", [None, "caller-langgraph-resume"])
async def test_langgraph_resume_propagates_effective_run_id_before_prepare(
    monkeypatch: pytest.MonkeyPatch,
    requested_run_id: str | None,
) -> None:
    from app.agent_runtime import langgraph_agent_stream_runner

    captured: dict[str, str] = {}

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool,
        run_id: str,
    ) -> tuple[str, list[str], dict[str, Any]]:
        captured["prepared"] = run_id
        assert messages_history == []
        assert is_trigger_mode is False
        return "agent", [], {"configurable": {}}

    async def fake_stream(_agent: str, _input: Any, _config: dict[str, Any], **kwargs: Any):
        captured["streamed"] = kwargs["run_id"]
        yield "resumed"

    def fake_langfuse(_cfg: AgentConfig, *, run_id: str, source: str) -> _FakeLangfuseContext:
        captured["langfuse"] = run_id
        assert source == "resume"
        return _FakeLangfuseContext()

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        langgraph_agent_stream_runner,
        "stream_agent_response_langgraph",
        fake_stream,
    )
    monkeypatch.setattr(langgraph_agent_stream_runner, "build_langfuse_run_context", fake_langfuse)

    chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.resume_agent_stream_langgraph(
            _cfg(),
            {"approved": True},
            run_id=requested_run_id,
        )
    ]

    assert chunks == ["resumed"]
    _assert_effective_run_id(captured, requested_run_id)
