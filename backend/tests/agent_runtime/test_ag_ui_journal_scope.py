"""The admitted physical run gets earlier commits even while the producer is idle."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from app.agent_runtime.journal_continuity import ag_ui_journal_scope, journal_commit_interval
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent


async def test_admitted_run_flushes_before_next_chunk_without_affecting_sibling() -> None:
    async def open_stream(
        run_id: str, seen: list[str], committed: asyncio.Event
    ) -> AsyncGenerator[str, None]:
        async def persist(batch: list[dict[str, Any]]) -> None:
            seen.extend(str(event["id"]) for event in batch)
            committed.set()

        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([]),
            {"messages": []},
            {"configurable": {"thread_id": "chat"}},
            run_id=run_id,
            persist_callback=persist,
        ):
            yield chunk

    active, other = asyncio.Event(), asyncio.Event()
    active_ids: list[str] = []
    other_ids: list[str] = []
    with ag_ui_journal_scope("admitted", version="1"):
        assert journal_commit_interval("sibling") is None
        async with (
            aclosing(open_stream("admitted", active_ids, active)) as admitted,
            aclosing(open_stream("sibling", other_ids, other)) as sibling,
        ):
            await anext(sibling)
            await anext(admitted)
            await asyncio.wait_for(active.wait(), timeout=1.0)
            assert len(active_ids) == 1
            assert not other.is_set()
            # Advance both real stream writers through their normal final flush.
            async for _ in admitted:
                pass
            async for _ in sibling:
                pass
        assert len(set(active_ids)) == len(active_ids) == 2
        assert len(set(other_ids)) == len(other_ids) == 2
        with ag_ui_journal_scope("admitted", version=None):
            assert journal_commit_interval("admitted") is None
    assert journal_commit_interval("admitted") is None
