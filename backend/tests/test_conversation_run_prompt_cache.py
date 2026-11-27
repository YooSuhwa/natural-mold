"""The owned worker grants prompt-cache routing only for the lifetime of the chat executor."""

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_agent_stream_runner import execute_agent_stream_langgraph
from app.agent_runtime.openai_prompt_cache import ConversationCacheChatOpenAI
from app.config import settings
from tests.authz import prepared_entry_testkit as kit


async def test_prepared_chat_revokes_cache_key_in_inherited_tasks_after_completion(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await kit.setup(db, tmp_path, monkeypatch, [AIMessage("done")])
    cfg = await kit.entry_cfg(harness)
    monkeypatch.setattr(
        settings, "openai_prompt_cache_org_ids", frozenset({cfg.org_context.org_id})
    )

    # Inspect outgoing payload construction at the scripted provider boundary.
    # The request is never sent, including after the worker has released its lease.
    probe = ConversationCacheChatOpenAI(
        model="gpt-4o-mini",
        api_key=SecretStr("fixture"),
        base_url="https://api.openai.com/v1",
        cache_routing_supported=True,
    )

    def request_key() -> str | None:
        return probe._get_request_payload("same input").get("prompt_cache_key")

    finished = asyncio.Event()
    continuations: list[asyncio.Task[str | None]] = []
    during: list[str | None] = []
    generate = kit.RecordingModel._agenerate

    async def after_worker() -> str | None:
        await finished.wait()
        return request_key()

    async def observed(model: kit.RecordingModel, *args: Any, **kwargs: Any) -> ChatResult:
        during.append(request_key())
        continuations.append(asyncio.create_task(after_worker()))
        return await generate(model, *args, **kwargs)

    monkeypatch.setattr(kit.RecordingModel, "_agenerate", observed)
    assert request_key() is None
    try:
        row = await kit.run_through_worker(
            harness,
            harness.seeded.run_id,
            cfg,
            {"messages": [{"role": "user", "content": "hi"}]},
            execute_agent_stream_langgraph,
        )
    finally:
        finished.set()
        after = await asyncio.gather(*continuations)
    assert row.status == "completed", row.error_code
    assert row.runtime_materialized_at is not None
    assert len(during) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", during[0] or "")
    assert after == [None]
