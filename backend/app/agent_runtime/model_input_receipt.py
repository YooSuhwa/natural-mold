"""Observe the human messages submitted to a model invocation and its accepted output."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackManager, BaseCallbackHandler, BaseCallbackManager
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables.config import patch_config, set_config_context
from langgraph.config import get_config


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _ModelDelivery(BaseCallbackHandler):
    """Retain matches only for completed model calls whose output the graph uses."""

    run_inline = True

    def __init__(self, expected: Mapping[str, tuple[str, str]]) -> None:
        self.expected = expected
        self.started: dict[UUID, set[str]] = {}
        self.finished: dict[str, set[str]] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        found: set[str] = set()
        for sequence in messages:
            for message in sequence:
                entry = self.expected.get(message.id or "")
                if (
                    isinstance(message, HumanMessage)
                    and entry is not None
                    and isinstance(message.content, str)
                    and text_digest(message.content) == entry[1]
                ):
                    found.add(entry[0])
        self.started[run_id] = found

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        matched = self.started.pop(run_id, set())
        for batch in response.generations:
            for generation in batch:
                if isinstance(generation, ChatGeneration) and generation.message.id:
                    self.finished.setdefault(generation.message.id, set()).update(matched)


async def observe_message_delivery(
    request: ModelRequest,
    handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    expected: Mapping[str, tuple[str, str]],
) -> tuple[ModelResponse, dict[str, set[str]]]:
    """Map returned model message IDs to the matching (receipt ID, text digest) inputs."""
    observer = _ModelDelivery(expected)
    config = get_config()
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        updated = callbacks.copy()
    else:
        handlers = list(callbacks or [])
        updated = AsyncCallbackManager(handlers=handlers.copy(), inheritable_handlers=handlers)
    updated.add_handler(observer, inherit=True)
    # Attach only to the current model invocation. Keep stream callbacks and the
    # graph's task context intact, including when a nested wrapper selects a model.
    with set_config_context(patch_config(config, callbacks=updated)) as ctx:
        response = await ctx.run(asyncio.ensure_future, handler(request))
    confirmed = {
        message.id: observer.finished[message.id]
        for message in response.result
        if message.id and observer.finished.get(message.id)
    }
    return response, confirmed
