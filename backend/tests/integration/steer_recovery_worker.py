"""Crash probe using the real graph, human input middleware and PostgreSQL event journal."""

from __future__ import annotations

import asyncio
import json
import sys
from functools import wraps
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from app.agent_runtime.chat_steer_middleware import RunSteerMiddleware
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.recovery_read_tool import RecoverySafeReadTool
from app.agent_runtime.recovery_tool_middleware import RecoveryToolMiddleware, with_recovery_fence
from app.agent_runtime.run_recovery_checkpoint import bind_recoverable_input
from app.agent_runtime.tool_factory import create_builtin_tool
from app.chat_recovery.fence import RunFence, fence_for_run, lock_run_fence, run_fence_scope
from app.config import settings
from app.database import async_session, tenant_session
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_steer_events import RunSteerReceiptFeed
from app.services.trace_storage import append_events
from app.tenancy.checkpoint_audit import run_configurable


def record(log: Path, event: dict[str, Any]) -> None:
    with log.open("a", encoding="utf-8") as stream:
        print(json.dumps(event), file=stream, flush=True)


def probe_graph(saver: AsyncPostgresSaver, log: Path, *, phase: str) -> Any:
    """The old worker blocks before its second read (or inside the unconfirmed model)."""

    class ObservedModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> ObservedModel:
            return self

        def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
            humans = [m.content for m in messages if isinstance(m, HumanMessage)]
            record(log, {"model": phase, "humans": humans})
            if phase == "unconfirmed" and len(humans) > 1:
                print("BREAK:model", flush=True)
                Event().wait()
            return super()._generate(messages, *args, **kwargs)

    lookup = create_builtin_tool("builtin:resolve_relative_date")
    assert isinstance(lookup, RecoverySafeReadTool) and lookup.coroutine is not None
    resolve_date = lookup.coroutine

    @wraps(resolve_date)
    async def observed_lookup(expression: str, **kwargs: Any) -> str:
        stage = {"오늘": "first", "내일": "second"}[expression]
        if phase != "successor":
            print(f"BREAK:lookup-{stage}", flush=True)
            if stage == "first":
                assert (await asyncio.to_thread(sys.stdin.readline)).strip() == "continue"
            elif stage == "second":
                await asyncio.Event().wait()
        result = await resolve_date(expression, **kwargs)
        record(log, {"lookup": stage, "worker": phase, "result": result})
        return result

    lookup.coroutine = observed_lookup
    responses: list[BaseMessage] = [
        AIMessage(
            id=f"ask-{stage}",
            content="",
            tool_calls=[
                {
                    "id": f"lookup-{stage}",
                    "name": lookup.name,
                    "args": {"expression": expression, "reference_date": "2026-02-01"},
                }
            ],
        )
        for stage, expression in (("first", "오늘"), ("second", "내일"))
    ]
    if phase == "successor":
        responses = [AIMessage(id="end-answer", content="both records found")]

    middleware, _ = with_recovery_fence([RunSteerMiddleware()], [])
    assert any(isinstance(item, RecoveryToolMiddleware) for item in middleware)
    return create_deep_agent(
        model=ObservedModel(responses=responses),
        tools=[lookup],
        middleware=middleware,
        checkpointer=saver,
    )


async def persist(fence: RunFence, events: list[dict[str, Any]]) -> None:
    async with tenant_session(fence.tenant) as db:
        await lock_run_fence(db, fence)
        await append_events(
            db,
            conversation_id=fence.conversation_id,
            assistant_msg_id=str(fence.run_id),
            events_chunk=events,
        )
        await db.commit()


async def first_worker(run_id: UUID, phase: str, log: Path) -> None:
    url = make_url(settings.database_url_sync)
    if not (url.database or "").startswith("moldy_pg_lane_") or phase not in {
        "confirmed",
        "unconfirmed",
    }:
        raise ValueError("Expected a disposable PostgreSQL lane and a known breakpoint")

    async with async_session() as db:
        run = await db.get(ConversationRun, run_id)
        assert run is not None
        fence = fence_for_run(run)
    async with AsyncPostgresSaver.from_conn_string(
        url.render_as_string(hide_password=False)
    ) as saver:
        await saver.setup()
        thread = str(fence.conversation_id)
        config = {"configurable": run_configurable(thread, run_id, fence.tenant.tenant_id)}
        with run_fence_scope(fence):
            graph = probe_graph(saver, log, phase=phase)
            human: list[BaseMessage] = [HumanMessage(content="compare the records")]
            bind_recoverable_input(human, str(run_id))

            async def save(events: list[dict[str, Any]]) -> None:
                await persist(fence, events)

            feed = RunSteerReceiptFeed.for_chat(
                fence.tenant, thread, str(run_id), source="chat", profile="standard"
            )
            assert feed is not None
            async for _ in stream_agent_response_langgraph(
                graph,
                human,
                config,
                run_id=str(run_id),
                steer_receipts=feed,
                persist_callback=save,
            ):
                pass
        raise AssertionError("First worker returned instead of pausing at its breakpoint")


if __name__ == "__main__":
    asyncio.run(first_worker(UUID(sys.argv[1]), sys.argv[2], Path(sys.argv[3])))
