"""A-10 harness: native Deep Agents model/guard/HITL/checkpointer behavior."""

from collections.abc import Callable, Sequence
from typing import Literal, Self, TypedDict, assert_never

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest, hook_config
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, JsonValue


class ScriptedModel(FakeMessagesListChatModel):
    """Only the external LLM is scripted; the middleware and graph are real."""

    def bind_tools(
        self,
        tools: Sequence[BaseTool | Callable[..., JsonValue] | type | dict[str, JsonValue]],
        *,
        tool_choice: str | None = None,
        **kwargs: JsonValue,
    ) -> Self:
        return self


class AuthResume(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["respond"]
    message: Literal["retry", "skip", "cancel"]


class GuardUpdate(TypedDict, total=False):
    messages: list[AnyMessage]
    jump_to: Literal["end"]


class ConnectionGuard(AgentMiddleware[AgentState[AIMessage], None, AIMessage]):
    """Mutable connection fixture proves native checkpoint/resume transitions."""

    def __init__(
        self,
        blocked: frozenset[str] = frozenset(),
        auth_tools: frozenset[str] = frozenset(),
        ready: bool = True,
    ) -> None:
        self.blocked = blocked
        self.auth_tools = auth_tools
        self.ready = ready

    @hook_config(can_jump_to=["end"])
    async def aafter_model(
        self, state: AgentState[AIMessage], runtime: Runtime[None]
    ) -> GuardUpdate | None:
        latest = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        if latest is None or not latest.tool_calls:
            return None
        completed = {m.tool_call_id for m in state["messages"] if isinstance(m, ToolMessage)}
        pending = [call for call in latest.tool_calls if call["id"] not in completed]
        updates: list[AnyMessage] = [
            ToolMessage(content="permission_required", tool_call_id=call["id"], status="error")
            for call in pending
            if call["name"] in self.blocked
        ]
        auth = [
            call
            for call in pending
            if call["name"] in self.auth_tools and call["name"] not in self.blocked
        ]
        while auth and not self.ready:
            decision = AuthResume.model_validate(
                interrupt(
                    {
                        "type": "auth_required",
                        "reason": "auth_needed",
                        "items": [
                            {
                                "resource_type": "tool",
                                "resource_name": "Fixture",
                                "tool_names": [call["name"] for call in auth],
                                "credential_id": "fixture-credential",
                                "fixable_by": "me",
                                "connect": {"kind": "binding"},
                            }
                        ],
                        "allowed_decisions": ["respond"],
                    }
                )
            )
            match decision.message:
                case "retry":
                    continue
                case "skip":
                    updates.extend(
                        ToolMessage(content="skipped", tool_call_id=call["id"], status="error")
                        for call in auth
                    )
                    break
                case "cancel":
                    return {
                        "messages": [
                            ToolMessage(
                                content="cancelled", tool_call_id=call["id"], status="error"
                            )
                            for call in pending
                        ],
                        "jump_to": "end",
                    }
                case _:
                    assert_never(decision.message)
        return {"messages": updates} if updates else None


def approval_pending(request: ToolCallRequest) -> bool:
    """Native HITL's when hook excludes calls already denied by the guard."""
    return all(
        not isinstance(message, ToolMessage) or message.tool_call_id != request.tool_call["id"]
        for message in request.state["messages"]
    )
