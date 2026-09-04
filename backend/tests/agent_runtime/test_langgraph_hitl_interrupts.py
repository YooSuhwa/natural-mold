from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime.langgraph_agent_stream_runner import (
    execute_agent_stream_langgraph,
    resume_agent_stream_langgraph,
)
from app.agent_runtime.runtime_component_builder import build_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import resolve_runtime_policy

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)


class FakeToolBindingChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolBindingChatModel:
        return self


@tool
def execute_in_skill(skill_directory: str, command: str) -> str:
    """Execute an allowed script inside a skill directory."""

    return f"executed {skill_directory}: {command}"


def _payloads(raw_chunks: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw in raw_chunks:
        data_line = next(line for line in raw.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        assert isinstance(payload, dict)
        payloads.append(payload)
    return payloads


def _message_text(payloads: list[dict[str, Any]]) -> str:
    """Collect user-visible assistant content from a v3 stream."""

    parts: list[str] = []
    for payload in payloads:
        if payload.get("method") != "messages":
            continue
        data = payload.get("params", {}).get("data")
        if not isinstance(data, dict):
            continue
        content = data.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


@pytest.mark.asyncio
async def test_langgraph_runner_emits_input_requested_for_execute_in_skill_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-execute-skill",
                        "name": "execute_in_skill",
                        "args": {
                            "skill_directory": "/skills/docx-document",
                            "command": "node scripts/create_docx.cjs",
                        },
                    }
                ],
            ),
            AIMessage(content="문서 생성이 완료되었습니다."),
        ]
    )
    agent = build_agent(
        model,
        [execute_in_skill],
        "Use execute_in_skill.",
        interrupt_on={"execute_in_skill": {"allowed_decisions": ["approve", "reject"]}},
        checkpointer=MemorySaver(),
        runtime_policy=resolve_runtime_policy(
            {
                "version": 1,
                "filesystem": {"mode": "artifact_write"},
                "todo": {"enabled": True},
                "summarization": {"mode": "auto"},
            }
        ),
    )

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool = False,
        run_id: str,
    ) -> tuple[Any, list[dict[str, str]], dict[str, Any]]:
        assert run_id == "run-hitl"
        return agent, messages_history, {"configurable": {"thread_id": "thread-hitl"}}

    from app.agent_runtime import langgraph_agent_stream_runner

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)

    chunks = [
        chunk
        async for chunk in execute_agent_stream_langgraph(
            AgentConfig(
                provider="fake",
                model_name="fake-chat",
                api_key=None,
                base_url=None,
                system_prompt="Use execute_in_skill.",
                tools_config=[],
                thread_id="thread-hitl",
            ),
            [{"role": "user", "content": "make a document"}],
            run_id="run-hitl",
        )
    ]

    input_requested = [
        payload for payload in _payloads(chunks) if payload.get("method") == "input.requested"
    ]
    assert input_requested, "LangGraph HITL interrupts must be projected as input.requested"
    data = input_requested[-1]["params"]["data"]
    assert data["interrupt_id"]
    assert data["payload"]["action_requests"][0]["name"] == "execute_in_skill"
    assert data["payload"]["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]

    resumed_chunks = [
        chunk
        async for chunk in resume_agent_stream_langgraph(
            AgentConfig(
                provider="fake",
                model_name="fake-chat",
                api_key=None,
                base_url=None,
                system_prompt="Use execute_in_skill.",
                tools_config=[],
                thread_id="thread-hitl",
            ),
            {"decisions": [{"type": "approve"}]},
            run_id="run-hitl",
        )
    ]
    resumed_payloads = _payloads(resumed_chunks)
    assert [
        payload for payload in resumed_payloads if payload.get("method") == "input.requested"
    ] == []
    assert "문서 생성이 완료되었습니다." in _message_text(resumed_payloads)
