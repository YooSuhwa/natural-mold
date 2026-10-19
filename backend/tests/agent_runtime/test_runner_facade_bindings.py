"""Public runner facades resolve patched collaborators at call time.

Each test calls the public facade once, replaces runner/hook/Langfuse/graph
attributes on the facade module, and proves the next call observes the
replacement by emitted chunk or object identity.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.agent_runtime import agent_stream_runner, langgraph_agent_stream_runner
from app.agent_runtime.runtime_config import AgentConfig
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID


def _config() -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="provider",
        model_name="model",
        api_key=None,
        base_url=None,
        system_prompt="prompt",
        tools_config=[],
        thread_id="runner-binding-thread",
        user_id=str(TEST_USER_ID),
    )


class _Hooks:
    def __init__(self, label: str, calls: list[str]) -> None:
        self.label = label
        self.calls = calls

    async def run_pre(self, _ctx: Any) -> None:
        self.calls.append(f"pre:{self.label}")

    async def run_post(self, _ctx: Any, _result: Any) -> None:
        self.calls.append(f"post:{self.label}")

    async def run_failure(self, _ctx: Any, _exc: BaseException) -> None:
        self.calls.append(f"failure:{self.label}")


def _langfuse(label: str, calls: list[str]):
    def build(_cfg: AgentConfig, **_kwargs: Any) -> Any:
        calls.append(f"langfuse:{label}")
        return SimpleNamespace(
            trace=None, configure_config=lambda config: config, flush=lambda: None
        )

    return build


def _install(
    monkeypatch: pytest.MonkeyPatch, module: Any, stream_name: str, label: str, calls: list[str]
) -> Any:
    agent = SimpleNamespace(
        ainvoke=_ainvoke(label),
        label=label,
    )

    async def prepare(_cfg: AgentConfig, **_kwargs: Any) -> tuple[Any, list, dict]:
        calls.append(f"prepare:{label}")
        return agent, [], {"configurable": {}}

    async def stream(graph: Any, *_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        yield f"{label}:{graph.label}"

    monkeypatch.setattr(module, "_prepare_agent", prepare)
    monkeypatch.setattr(module, stream_name, stream)
    monkeypatch.setattr(module, "hooks", _Hooks(label, calls))
    monkeypatch.setattr(module, "build_langfuse_run_context", _langfuse(label, calls))
    return agent


def _ainvoke(label: str):
    async def ainvoke(_payload: Any, *, config: Any) -> dict[str, Any]:
        return {"messages": [AIMessage(content=f"invoke-{label}")]}

    return ainvoke


async def _collect(stream: AsyncGenerator[str, None]) -> list[str]:
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_stream_facade_observes_replacement_after_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    module = agent_stream_runner
    _install(monkeypatch, module, "stream_agent_response", "first", calls)
    first = await _collect(module.execute_agent_stream(_config(), []))
    first_invoke = await module.execute_agent_invoke(_config(), [])

    _install(monkeypatch, module, "stream_agent_response", "second", calls)
    second = await _collect(module.resume_agent_stream(_config(), {"decision": "approve"}))
    second_invoke = await module.execute_agent_invoke(_config(), [])

    assert (first, second) == (["first:first"], ["second:second"])
    assert (first_invoke, second_invoke) == ("invoke-first", "invoke-second")
    assert calls == [
        *("prepare:first", "langfuse:first", "pre:first", "post:first"),
        *("prepare:first", "langfuse:first", "pre:first", "post:first"),
        *("prepare:second", "langfuse:second", "pre:second", "post:second"),
        *("prepare:second", "langfuse:second", "pre:second", "post:second"),
    ]


@pytest.mark.asyncio
async def test_langgraph_facade_observes_replacement_after_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    module = langgraph_agent_stream_runner
    _install(monkeypatch, module, "stream_agent_response_langgraph", "first", calls)
    first = await _collect(module.execute_agent_stream_langgraph(_config(), [], run_id="run-1"))

    _install(monkeypatch, module, "stream_agent_response_langgraph", "second", calls)
    sink: list[str] = []
    second = await _collect(
        module.resume_agent_stream_langgraph(_config(), True, msg_id_sink=sink, run_id="run-2")
    )

    assert (first, second) == (["first:first"], ["second:second"])
    assert sink == ["run-2"]
    assert calls == [
        *("prepare:first", "langfuse:first", "pre:first", "post:first"),
        *("prepare:second", "langfuse:second", "pre:second", "post:second"),
    ]
