from __future__ import annotations

import json
from dataclasses import replace
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
from app.tenancy.checkpoint_audit import checkpoint_tenant
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)


class FakeToolBindingChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolBindingChatModel:
        return self


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
@pytest.mark.parametrize(
    ("pin", "next_review"),
    [(False, False), (True, False), (True, True)],
    ids=["unkeyed", "pinned-completion", "pinned-next-review"],
)
async def test_langgraph_runner_emits_input_requested_for_execute_in_skill_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    pin: bool,
    next_review: bool,
) -> None:
    executed: list[str] = []

    tool_name = "record_document_action" if pin else "execute_in_skill"

    @tool(tool_name)
    def action(skill_directory: str, command: str) -> str:
        """Record an approved script execution."""
        executed.append(command)
        return f"executed {skill_directory}: {command}"

    def tool_call(command: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": f"call-{command}",
                    "name": tool_name,
                    "args": {"skill_directory": "/skills/docx-document", "command": command},
                }
            ],
        )

    responses = [tool_call("create")]
    if next_review:
        responses.append(tool_call("export"))
    responses.append(AIMessage(content="문서 생성이 완료되었습니다."))
    model = FakeToolBindingChatModel(responses=responses)
    saver = MemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer._checkpointer", saver)
    agent = build_agent(
        model,
        [action],
        f"Use {tool_name}.",
        interrupt_on={tool_name: {"allowed_decisions": ["approve", "reject"]}},
        checkpointer=saver,
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
        cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool = False,
        run_id: str,
    ) -> tuple[Any, list[dict[str, str]], dict[str, Any]]:
        configurable = {
            "thread_id": cfg.thread_id,
            "moldy_run_id": run_id,
            **checkpoint_tenant(cfg.org_context.tenant_id),
        }
        if cfg.checkpoint_id is not None:
            assert run_id == "run-resume"
            configurable["checkpoint_id"] = cfg.checkpoint_id
        return agent, messages_history, {"configurable": configurable}

    from app.agent_runtime import langgraph_agent_stream_runner

    monkeypatch.setattr(langgraph_agent_stream_runner, "_prepare_agent", fake_prepare_agent)

    cfg = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt=f"Use {tool_name}.",
        tools_config=[],
        thread_id="thread-hitl",
        org_context=unit_context(TEST_USER_ID),
    )
    chunks = [
        chunk
        async for chunk in execute_agent_stream_langgraph(
            cfg,
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
    assert data["payload"]["action_requests"][0]["name"] == tool_name
    assert data["payload"]["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]

    latest = {"configurable": {"thread_id": cfg.thread_id}}
    parent = await agent.aget_state(latest)
    interrupt_id = data["interrupt_id"]
    assert [i.id for task in parent.tasks for i in task.interrupts] == [interrupt_id]
    assert executed == []
    answers = {"decisions": [{"type": "approve"}]}
    checkpoint_id = parent.config["configurable"]["checkpoint_id"] if pin else None
    resumed = _payloads(
        [
            chunk
            async for chunk in resume_agent_stream_langgraph(
                replace(cfg, checkpoint_id=checkpoint_id),
                {interrupt_id: answers} if pin else answers,
                run_id="run-resume",
            )
        ]
    )
    if pin:
        assert executed == ["create"]
    head = await agent.aget_state(latest)
    assert (
        head.config["configurable"]["checkpoint_id"]
        != parent.config["configurable"]["checkpoint_id"]
    )
    pending = [i.id for task in head.tasks for i in task.interrupts]
    requests = [e["params"]["data"] for e in resumed if e["method"] == "input.requested"]
    projected = [request["interrupt_id"] for request in requests]
    if next_review:
        assert len(pending) == 1 and pending[0] != interrupt_id
        assert projected == pending
        assert requests[0]["payload"]["action_requests"][0]["args"]["command"] == "export"
    else:
        assert not head.next and pending == []
        assert projected == []
        assert "문서 생성이 완료되었습니다." in _message_text(resumed)
    assert resumed[-1]["method"] == "lifecycle"
    assert resumed[-1]["params"]["data"] == {"event": "interrupted" if next_review else "completed"}
