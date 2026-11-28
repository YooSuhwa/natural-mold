from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware.internal_call_transformer import internal_call_metadata
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.config import settings
from tests.agent_runtime.langgraph_streaming_fixtures import sse_payload


@pytest.mark.asyncio
async def test_child_summary_ignores_interleaved_root_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "compaction_marker_enabled", True)
    model = GenericFakeChatModel(messages=iter([AIMessage(content="internal summary")]))
    running_seen = asyncio.Event()

    async def interleaved(
        _input: Any, *, config: RunnableConfig, **_: Any
    ) -> AsyncIterator[tuple[tuple[str, ...], str, dict[str, Any]]]:
        yield (), "values", {}
        yield (
            ("child:1",),
            "values",
            {"_summarization_event": {"cutoff_index": 2, "cutoff_message_id": "old-child-cutoff"}},
        )
        await model.ainvoke(
            "Summarize the child's conversation",
            config={
                **config,
                "metadata": {
                    **internal_call_metadata(),
                    "lc_source": "summarization",
                    "langgraph_checkpoint_ns": "child:1|model:2",
                },
            },
        )
        await running_seen.wait()
        yield (), "values", {"_summarization_event": {"cutoff_index": 99}}
        yield (
            ("child:1",),
            "values",
            {"_summarization_event": {"cutoff_index": 2, "cutoff_message_id": "new-child-cutoff"}},
        )

    markers = []
    async with asyncio.timeout(10):
        async for frame in stream_agent_response_langgraph(
            SimpleNamespace(astream=interleaved), [], {}, run_id="parent-run"
        ):
            event = sse_payload(frame)
            data = event["params"]["data"]
            if event["method"] == "custom" and data.get("name") == "moldy.compaction":
                markers.append(data["payload"])
                if data["payload"]["state"] == "running":
                    running_seen.set()

    assert markers == [{"state": "running"}, {"state": "done", "cutoff_index": 2}]
