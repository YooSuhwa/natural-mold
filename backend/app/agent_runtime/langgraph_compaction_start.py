"""Merge the summary model's content-free start notification into the chat stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from langchain.agents.middleware.internal_call_transformer import (
    INTERNAL_CALL_METADATA_KEY,
    internal_call_metadata,
)
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langgraph.pregel._messages import graph_namespace

if TYPE_CHECKING:
    from app.agent_runtime.langgraph_stream_ingestion import IngestedProtocolEvent


@dataclass(frozen=True, slots=True)
class SummaryStart:
    namespace: tuple[str, ...]


def is_internal_summary(metadata: Any) -> bool:
    return (
        isinstance(metadata, Mapping)
        and metadata.get("lc_source") == "summarization"
        and metadata.get(INTERNAL_CALL_METADATA_KEY)
        == internal_call_metadata()[INTERNAL_CALL_METADATA_KEY]
    )


class _SummaryCallback(BaseCallbackHandler):
    run_inline = True

    def __init__(self, notify: Callable[[SummaryStart], None]) -> None:
        self.notify = notify
        self.loop = asyncio.get_running_loop()

    def on_chat_model_start(
        self, _serialized: Any, _messages: Any, *, metadata: dict[str, Any] | None = None, **_: Any
    ) -> None:
        if metadata and is_internal_summary(metadata):
            namespace = graph_namespace(metadata) if "langgraph_checkpoint_ns" in metadata else ()
            self.loop.call_soon_threadsafe(self.notify, SummaryStart(namespace))


async def with_compaction_starts(
    events: Callable[[dict[str, Any]], AsyncGenerator[IngestedProtocolEvent, None]],
    config: dict[str, Any],
    *,
    enabled: bool,
) -> AsyncGenerator[IngestedProtocolEvent | SummaryStart, None]:
    if not enabled:
        async with aclosing(events(config)) as source:
            async for item in source:
                yield item
        return

    # The graph runs in one task for its entire lifetime. A single pending graph
    # event plus the one-shot notification bound the queue even for a slow reader.
    queue: asyncio.Queue[IngestedProtocolEvent | SummaryStart | None] = asyncio.Queue()
    consumed = asyncio.Event()
    started = False

    def notify(start: SummaryStart) -> None:
        nonlocal started
        if not started:
            started = True
            queue.put_nowait(start)

    callback = _SummaryCallback(notify)
    configured = dict(config)
    configured["callbacks"] = merge_configs(
        cast(RunnableConfig, {"callbacks": config.get("callbacks")}), {"callbacks": [callback]}
    ).get("callbacks")

    async def pump() -> None:
        try:
            async with aclosing(events(configured)) as source:
                async for item in source:
                    consumed.clear()
                    queue.put_nowait(item)
                    await consumed.wait()
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(pump())
    try:
        while (item := await queue.get()) is not None:
            yield item
            if not isinstance(item, SummaryStart):
                consumed.set()
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
