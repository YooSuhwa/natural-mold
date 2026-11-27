"""A resumed LangGraph model step shares its persisted provider-attempt budget."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.recovery_checkpoint_saver import RecoveryCheckpointSaver
from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware
from app.agent_runtime.runtime_component_builder import _build_model_candidates
from app.agent_runtime.stream_retry_authority import check_stream_attempt
from app.authz.conversation_run_scope import RunConversationTarget, RunExecutionTarget
from app.chat_recovery.fence import RunFence, claim_run_owner, run_fence_scope
from app.chat_recovery.model_attempts import RecoveryModelAttemptBlocked, new_model_attempt_budget
from app.config import settings
from app.database import tenant_session
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service
from app.services.conversation_run_task_registry import RunTaskRegistry
from app.services.stream_attempt_owner import stream_attempt_owner
from app.tenancy.checkpoint_audit import run_configurable
from tests.agent_runtime.test_direct_stream_retry import (
    CONCURRENCY,
    Gateway,
    _chunk,
    _config,
    _error,
    _stream,
)
from tests.agent_runtime.test_direct_stream_retry import gateway as gateway
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_conversation_queue_concurrency import (
    _postgres_session_factory,
    _seed_conversation,
)

pytestmark = pytest.mark.integration


@dataclass
class BudgetRun:
    target: RunExecutionTarget
    sessions: async_sessionmaker[AsyncSession]
    saver: AsyncPostgresSaver

    async def claim(self) -> RunFence:
        async with self.sessions() as db:
            row = await db.get(ConversationRun, self.target.run_id, with_for_update=True)
            assert row is not None
            owner = claim_run_owner(row, "replacement")
            await db.commit()
            return owner

    def graph(
        self,
        owner: RunFence,
        *,
        admit: Callable[[], Awaitable[None]] = check_stream_attempt,
        tools: Sequence[BaseTool] = (),
    ) -> Any:
        models = _build_model_candidates(_config())
        retry = DirectStreamRetryMiddleware(
            fallbacks=models[1:], check_authority=admit, initial_delay=0
        )
        return create_agent(
            model=models[0],
            middleware=[retry],
            tools=list(tools),
            checkpointer=RecoveryCheckpointSaver(self.saver, owner),
        )

    @property
    def config(self) -> RunnableConfig:
        target = self.target.conversation
        return {
            "configurable": run_configurable(
                str(target.conversation_id), self.target.run_id, target.context.tenant_id
            )
        }

    async def stream(self, graph: Any, owner: RunFence, *, resume: bool) -> str:
        registry = RunTaskRegistry(worker_instance_id=owner.worker_instance_id)
        registry.start_current(owner.run_id)
        registry.bind_fence(owner)
        data = None if resume else {"messages": [{"role": "user", "content": "Count this batch."}]}
        parts: list[str] = []
        with run_fence_scope(owner), stream_attempt_owner(self.target, registry):
            async for chunk, _metadata in graph.astream(
                data, self.config, stream_mode="messages", durability="sync"
            ):
                if isinstance(chunk, AIMessageChunk) and isinstance(chunk.content, str):
                    parts.append(chunk.content)
        return "".join(parts)


@pytest.fixture
async def run(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AsyncIterator[BudgetRun]:
    sessions, engine = _postgres_session_factory()
    monkeypatch.setattr(
        "app.services.stream_attempt_owner.tenant_session", partial(tenant_session, maker=sessions)
    )
    monkeypatch.setattr(settings, "chat_run_recovery_enabled", True)
    try:
        user, agent, conversation = await _seed_conversation(sessions)
        context = unit_context(user)
        async with sessions() as db:
            row = await conversation_run_service.create_run(
                db,
                conversation_id=conversation,
                agent_id=agent,
                context=context,
                source="chat",
                input_preview="Count this batch.",
            )
            await conversation_run_service.transition_run(
                db, row, "running", worker_instance_id="replacement"
            )
            if getattr(request, "param", "enrolled") == "enrolled":
                row.recovery_model_attempts = new_model_attempt_budget(limit=3)
            await db.commit()
            target = RunExecutionTarget(row.id, RunConversationTarget(conversation, agent, context))
        url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            yield BudgetRun(target, sessions, saver)
    finally:
        await engine.dispose()


async def _stop_before_retry(
    run: BudgetRun, owner: RunFence, gateway: Gateway, *, resume: bool
) -> tuple[str, str]:
    previous = len(gateway.models)
    backoff = asyncio.Event()

    async def admit() -> None:
        if len(gateway.models) > previous:
            # The SDK has parsed a retriable error and the next send has not yet been admitted.
            backoff.set()
            await asyncio.Event().wait()
        await check_stream_attempt()

    graph = run.graph(owner, admit=admit)
    task = asyncio.create_task(run.stream(graph, owner, resume=resume))
    try:
        async with asyncio.timeout(10):
            await backoff.wait()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(gateway.models) == previous + 1
    snapshot = await graph.aget_state(run.config)
    (pending,) = snapshot.tasks
    assert pending.name == "model"
    return snapshot.config["configurable"]["checkpoint_id"], pending.id


async def test_same_pending_model_sends_only_three_times_across_three_owners(
    run: BudgetRun, gateway: Gateway
) -> None:
    gateway.respond = lambda: _stream(_error(CONCURRENCY))
    first = await run.claim()
    head = await _stop_before_retry(run, first, gateway, resume=False)
    second = await run.claim()
    assert head == await _stop_before_retry(run, second, gateway, resume=True)
    third = await run.claim()
    assert [owner.generation for owner in (first, second, third)] == [1, 2, 3]
    assert len({owner.owner_token for owner in (first, second, third)}) == 3
    with pytest.raises(RecoveryModelAttemptBlocked) as blocked:
        await asyncio.create_task(run.stream(run.graph(third), third, resume=True))
    assert blocked.value.code == "recovery_model_attempt_limit"
    assert len(gateway.models) == 3


async def test_model_step_after_completed_tool_has_its_own_provider_budget(
    run: BudgetRun, gateway: Gateway
) -> None:
    calls: list[int] = []

    @tool
    def count_items(value: int) -> str:
        """Count the items once."""
        calls.append(value)
        return str(value)

    tool_call = {
        "id": "count",
        "index": 0,
        "type": "function",
        "function": {"name": "count_items", "arguments": '{"value":3}'},
    }
    replies = iter(
        (
            _stream(_error(CONCURRENCY)),
            _stream(_error(CONCURRENCY)),
            _stream(_chunk(tool_calls=[tool_call])),
            _stream(_chunk(content="finished")),
        )
    )
    gateway.respond = lambda: next(replies)
    owner = await run.claim()
    graph = run.graph(owner, tools=[count_items])
    assert await asyncio.create_task(run.stream(graph, owner, resume=False)) == "finished"
    assert len(gateway.models) == 4
    assert calls == [3]


@pytest.mark.parametrize("run", ["legacy"], indirect=True)
async def test_old_pending_step_with_unknown_send_count_stops_before_recovery_send(
    run: BudgetRun, gateway: Gateway
) -> None:
    gateway.respond = lambda: _stream(_error(CONCURRENCY))
    first = await run.claim()
    await _stop_before_retry(run, first, gateway, resume=False)
    replacement = await run.claim()
    with pytest.raises(RecoveryModelAttemptBlocked) as blocked:
        await asyncio.create_task(run.stream(run.graph(replacement), replacement, resume=True))
    assert blocked.value.code == "recovery_model_attempt_unproven"
    assert len(gateway.models) == 1
