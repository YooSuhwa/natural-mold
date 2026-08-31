"""Golden contract for a real Deep Agents HITL interrupt and resume cycle."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent_runtime import langgraph_agent_stream_runner
from app.agent_runtime.runtime_config import AgentConfig
from tests.agent_runtime.langgraph_streaming_fixtures import sse_payload
from tests.agent_runtime.runtime_contract_helpers import (
    assert_contract_matches,
    contract_diff_paths,
    load_contract_fixture,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)

_RUN_ID = "run-resume-contract-1"
_THREAD_ID = "thread-resume-contract-1"
_RESUME_VALUE = {"decisions": [{"type": "approve"}]}


class FakeToolBindingChatModel(FakeMessagesListChatModel):
    """Keep the deterministic fake model compatible with create_deep_agent."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolBindingChatModel:
        return self


def _cfg() -> AgentConfig:
    return AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="Use execute_in_skill.",
        tools_config=[],
        thread_id=_THREAD_ID,
    )


def _payloads(chunks: list[str]) -> list[dict[str, Any]]:
    return [sse_payload(chunk) for chunk in chunks]


def _message_text(payloads: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for payload in payloads:
        if payload.get("method") != "messages":
            continue
        data = payload["params"]["data"]
        content = data.get("content") if isinstance(data, dict) else None
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


async def _collect_manifest(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    tool_calls: list[dict[str, str]] = []

    @tool
    def execute_in_skill(skill_directory: str, command: str) -> str:
        """Execute an allowed script inside a skill directory."""

        tool_calls.append({"skill_directory": skill_directory, "command": command})
        return "document created"

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
            AIMessage(content="문서 생성이 완료되었습니다.", id="assistant-final-1"),
        ]
    )
    checkpointer = MemorySaver()
    agent = create_deep_agent(
        model=model,
        tools=[execute_in_skill],
        system_prompt="Use execute_in_skill.",
        interrupt_on={"execute_in_skill": {"allowed_decisions": ["approve", "reject"]}},
        checkpointer=checkpointer,
    )
    prepared: list[dict[str, Any]] = []

    async def fake_prepare_agent(
        _cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool = False,
        run_id: str,
    ) -> tuple[Any, list[dict[str, str]], dict[str, Any]]:
        prepared.append(
            {
                "agent_identity": id(agent),
                "checkpointer_identity": id(checkpointer),
                "history_length": len(messages_history),
                "run_id": run_id,
                "thread_id": _THREAD_ID,
                "trigger_mode": is_trigger_mode,
            }
        )
        return agent, messages_history, {"configurable": {"thread_id": _THREAD_ID}}

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)

    initial_chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.execute_agent_stream_langgraph(
            _cfg(),
            [{"role": "user", "content": "문서를 만들어줘"}],
            run_id=_RUN_ID,
        )
    ]
    initial_payloads = _payloads(initial_chunks)
    requests = [event for event in initial_payloads if event.get("method") == "input.requested"]
    assert len(requests) == 1
    request = requests[0]["params"]["data"]
    initial_tool_execution_count = len(tool_calls)

    original_command = Command
    commands: list[Command[Any]] = []

    def capture_command(*args: Any, **kwargs: Any) -> Command[Any]:
        command = original_command(*args, **kwargs)
        commands.append(command)
        return command

    monkeypatch.setattr(langgraph_agent_stream_runner, "Command", capture_command)
    resumed_chunks = [
        chunk
        async for chunk in langgraph_agent_stream_runner.resume_agent_stream_langgraph(
            _cfg(), _RESUME_VALUE, run_id=_RUN_ID
        )
    ]
    resumed_payloads = _payloads(resumed_chunks)
    assert len(commands) == 1

    return {
        "initial": {
            "methods": [event["method"] for event in initial_payloads],
            "interrupt": {
                "action": request["payload"]["action_requests"][0],
                "allowed_decisions": request["payload"]["review_configs"][0]["allowed_decisions"],
            },
            "input_requested_count": len(requests),
            "tool_execution_count": initial_tool_execution_count,
        },
        "resume": {
            "command": {
                "module": type(commands[0]).__module__,
                "name": type(commands[0]).__name__,
                "resume": commands[0].resume,
            },
            "methods": [event["method"] for event in resumed_payloads],
            "final_response": _message_text(resumed_payloads),
            "input_requested_count": sum(
                event.get("method") == "input.requested" for event in resumed_payloads
            ),
            "tool_calls": tool_calls,
        },
        "continuity": {
            "same_agent": prepared[0]["agent_identity"] == prepared[1]["agent_identity"],
            "same_checkpointer": prepared[0]["checkpointer_identity"]
            == prepared[1]["checkpointer_identity"],
            "thread_ids": [call["thread_id"] for call in prepared],
            "history_lengths": [call["history_length"] for call in prepared],
            "run_ids": [call["run_id"] for call in prepared],
            "trigger_modes": [call["trigger_mode"] for call in prepared],
        },
    }


@pytest.mark.asyncio
async def test_runtime_resume_contract_matches_real_hitl_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_contract_matches("runtime_resume_v1.json", await _collect_manifest(monkeypatch))


@pytest.mark.asyncio
async def test_runtime_resume_contract_rejects_behavioral_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = load_contract_fixture("runtime_resume_v1.json")
    manifest = await _collect_manifest(monkeypatch)

    repeated_interrupt = deepcopy(manifest)
    repeated_interrupt["resume"]["input_requested_count"] = 1
    assert contract_diff_paths(expected, repeated_interrupt) == ["$.resume.input_requested_count"]

    wrong_resume = deepcopy(manifest)
    wrong_resume["resume"]["command"]["resume"] = {"decisions": [{"type": "reject"}]}
    assert contract_diff_paths(expected, wrong_resume) == [
        "$.resume.command.resume.decisions[0].type"
    ]

    changed_thread = deepcopy(manifest)
    changed_thread["continuity"]["thread_ids"][1] = "thread-other"
    assert contract_diff_paths(expected, changed_thread) == ["$.continuity.thread_ids[1]"]

    reordered_resume = deepcopy(manifest)
    reordered_resume["resume"]["methods"][0], reordered_resume["resume"]["methods"][1] = (
        reordered_resume["resume"]["methods"][1],
        reordered_resume["resume"]["methods"][0],
    )
    assert contract_diff_paths(expected, reordered_resume) == [
        "$.resume.methods[0]",
        "$.resume.methods[1]",
    ]

    changed_tool_call = deepcopy(manifest)
    changed_tool_call["resume"]["tool_calls"][0]["command"] = "unexpected command"
    assert contract_diff_paths(expected, changed_tool_call) == ["$.resume.tool_calls[0].command"]
