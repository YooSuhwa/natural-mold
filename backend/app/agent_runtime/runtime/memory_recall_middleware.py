"""Automatic recall as a one-off context message (memory policy plan 15, 5.4-4).

The block computed at run start rides every model call of the root agent, placed just
before the latest user message. It is added to the model request only: the graph state,
the checkpoint, the stored events and traces never hold it, and the system prompt stays
the same across turns (prompt caching). Children get none: this middleware is installed in
the root agent's slot only. Placed before the user's message, the guardrail's input check
still reads what the person typed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AnyMessage, HumanMessage

RECALL_MESSAGE_NAME = "moldy_recalled_memories"


def with_recall(messages: Sequence[AnyMessage], block: str) -> list[AnyMessage]:
    out = list(messages)
    note = HumanMessage(content=block, name=RECALL_MESSAGE_NAME)
    for index in range(len(out) - 1, -1, -1):
        if isinstance(out[index], HumanMessage):
            out.insert(index, note)
            return out
    out.append(note)
    return out


class MemoryRecallMiddleware(AgentMiddleware):
    def __init__(self, block: str) -> None:
        super().__init__()
        self.block = block

    @property
    def name(self) -> str:
        return "MemoryRecallMiddleware"

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        return handler(request.override(messages=with_recall(request.messages, self.block)))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(request.override(messages=with_recall(request.messages, self.block)))


__all__ = ["RECALL_MESSAGE_NAME", "MemoryRecallMiddleware", "with_recall"]
