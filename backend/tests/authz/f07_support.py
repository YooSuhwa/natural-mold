"""Graph helpers for the F-07 ConnectionGuard tests (fake model, in-memory checkpointer)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent_runtime.runtime.connection_guard import ConnectionGuardMiddleware
from app.agent_runtime.runtime.connection_guard_policy import (
    ConnectionGuardPolicy,
    CredentialState,
    GuardedTool,
    GuardMode,
)
from app.agent_runtime.runtime_component_builder import build_agent

ME, OTHER = str(uuid.uuid4()), str(uuid.uuid4())


class FakeToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolModel:
        return self


def calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": f"call-{n}", "name": n, "args": {"q": n}} for n in names],
    )


@dataclass
class Ledger:
    ran: list[str] = field(default_factory=list)
    states: dict[str, CredentialState] = field(default_factory=dict)
    reads: int = 0
    flip_after: int | None = None  # credentials become active after N reads

    async def read(self, tools: Sequence[GuardedTool]) -> Mapping[str, CredentialState]:
        self.reads += 1
        if self.flip_after is not None and self.reads > self.flip_after:
            return {t.credential_id: CredentialState("active", ME, "mcp_oauth2") for t in tools}
        return {k: v for k, v in self.states.items() if k in {t.credential_id for t in tools}}


def plain_tool(ledger: Ledger, name: str) -> BaseTool:
    async def run(q: str) -> str:
        ledger.ran.append(name)
        return f"{name} ok"

    return StructuredTool.from_function(coroutine=run, name=name, description=f"{name} tool")


def auth_failing_tool(ledger: Ledger, name: str, credential_id: str, owner: str = "me") -> BaseTool:
    """Behaves like F-12's ``MCPToolWithRetry`` on a 401 (artifact with an 11.5 payload)."""

    async def run(q: str) -> tuple[str, dict[str, Any]]:
        ledger.ran.append(name)
        item = {
            "resource_type": "mcp_server",
            "resource_name": "Slack",
            "tool_names": [name],
            "credential_id": credential_id,
            "fixable_by": owner,
            "connect": {"kind": "oauth"},
        }
        interrupt = {"type": "auth_required", "reason": "auth_needed", "items": [item]}
        artifact = {"mcp_auth_error": {"kind": "auth_required", "interrupt": interrupt}}
        return f"[MCP Auth Error] {name}: auth_required (HTTP 401)", artifact

    return StructuredTool.from_function(
        coroutine=run, name=name, description=name, response_format="content_and_artifact"
    )


def guarded(
    name: str, credential_id: str, mode: str = "shared", account: str | None = None
) -> GuardedTool:
    return GuardedTool(
        name, "mcp_server", "Slack", credential_id, mode, str(uuid.uuid4()), None, account
    )


def guard(
    ledger: Ledger, *, mode: GuardMode = "interrupt", **policy: Any
) -> ConnectionGuardMiddleware:
    return ConnectionGuardMiddleware(
        ConnectionGuardPolicy(mode=mode, actor_user_id=ME, read_states=ledger.read, **policy)
    )


@dataclass
class Run:
    agent: Any
    config: dict[str, Any]

    async def start(self) -> dict[str, Any]:
        return await self.agent.ainvoke({"messages": [HumanMessage("go")]}, self.config)

    async def resume(self, value: Any) -> dict[str, Any]:
        return await self.agent.ainvoke(Command(resume=value), self.config)


def run_graph(
    responses: list[AIMessage],
    tools: list[BaseTool],
    middleware: list[Any],
    interrupt_on: dict[str, Any] | None = None,
) -> Run:
    agent = build_agent(
        FakeToolModel(responses=list[BaseMessage](responses)),
        tools,
        "test",
        middleware=middleware,
        interrupt_on=interrupt_on,
        checkpointer=MemorySaver(),
    )
    return Run(agent, {"configurable": {"thread_id": str(uuid.uuid4())}})


def interrupts(result: Mapping[str, Any]) -> list[Any]:
    return [item.value for item in result.get("__interrupt__", ())]


def tool_messages(result: Mapping[str, Any]) -> dict[str, ToolMessage]:
    return {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}


def respond(message: str) -> dict[str, Any]:
    return {"decisions": [{"type": "respond", "message": message}]}
