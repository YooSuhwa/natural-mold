"""Create an enrolled, expired run in the disposable PostgreSQL contract lane."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime import checkpointer
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.chat_recovery.identity import recovery_runtime_digest
from app.config import settings
from app.credentials.creation import CredentialCreation
from app.database import SessionTenant, platform_sessions, tenant_session
from app.guardrails.pinning import pin_run_guardrails
from app.models.agent import Agent
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.user import User
from app.services import conversation_run_service
from app.services.conversation_agent_context import resolve_agent_context
from app.services.conversation_run_fence import RunFence, claim_run_owner
from app.services.conversation_run_queue_worker_metadata import queue_current_user
from app.services.conversation_run_recovery import recover_stale_chat_runs
from app.services.conversation_run_task_registry import RunTaskRegistry
from app.tenancy.checkpoint_audit import checkpoint_tenant
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.integration.test_conversation_queue_concurrency import (
    _postgres_session_factory,
    _seed_conversation,
)


@dataclass(frozen=True)
class StaleRun:
    sessions: async_sessionmaker[AsyncSession]
    fence: RunFence

    async def sweep(self, worker: str) -> int:
        return await recover_stale_chat_runs(
            session_factory=platform_sessions("conversation_run.lifecycle", maker=self.sessions),
            company_session=self._tenant_session,
            stale_before=utc_now_naive() - timedelta(minutes=1),
            registry=RunTaskRegistry(worker_instance_id=worker),
        )

    def _tenant_session(self, scope: SessionTenant) -> AsyncSession:
        return tenant_session(scope, maker=self.sessions)


@contextlib.asynccontextmanager
async def stale_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[StaleRun]:
    sessions, engine = _postgres_session_factory()
    # Exercise the production credential lookup; the root unit fixture substitutes a model key.
    monkeypatch.setattr(
        "app.services.conversation_stream_service.resolve_llm_api_key_for_agent",
        resolve_llm_api_key_for_agent,
    )
    monkeypatch.setattr(settings, "chat_run_recovery_enabled", True)
    monkeypatch.setattr(settings, "chat_run_recovery_max_attempts", 2)
    created_run: uuid.UUID | None = None
    try:
        await checkpointer.init_checkpointer(settings.database_url_sync)
        user_id, agent_id, conversation_id = await _seed_conversation(sessions)
        context = unit_context(user_id)
        async with sessions() as db:
            credential = await create_test_credential(
                db,
                CredentialCreation("openai", "fixture-key", {"api_key": "fixture-only"}),
                context,
            )
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            agent.llm_credential_id = credential.id
            await db.commit()
            run = await conversation_run_service.create_run(
                db,
                conversation_id=conversation_id,
                agent_id=agent_id,
                context=context,
                source="chat",
                input_preview="local read",
            )
            created_run = run.id
            await conversation_run_service.transition_run(
                db, run, "running", worker_instance_id="lost"
            )
            await db.commit()
            user = await db.get(User, user_id)
            assert user is not None
            cfg = await resolve_agent_context(
                db, conversation_id, queue_current_user(user), context=context
            )
            guard = await pin_run_guardrails(
                db,
                run_id=run.id,
                conversation_id=conversation_id,
                context=context,
                agent_id=agent_id,
                runtime_profile=cfg.runtime_profile,
                resume=False,
            )
            run = await db.get(ConversationRun, run.id, with_for_update=True)
            assert run is not None
            run.recovery_runtime_digest = recovery_runtime_digest(
                replace(
                    cfg,
                    guardrail_rules=guard.rules,
                    guardrail_resolved=True,
                    tool_risk=guard.tool_risk,
                )
            )
            assert run.recovery_runtime_digest
            # These tests stop at the launch boundary. The compiled graph's preparation
            # digest is checked by the receiving worker before its first model/tool call.
            run.metadata_json = {
                **(run.metadata_json or {}),
                "recovery_preparation_digest": "1" * 64,
            }
            fence = claim_run_owner(run, "lost")
            run.heartbeat_at = utc_now_naive() - timedelta(hours=1)
            await db.commit()
        yield StaleRun(sessions, fence)
    finally:
        try:
            if created_run is not None:
                async with sessions() as db:
                    await db.execute(
                        delete(ConversationRun).where(ConversationRun.id == created_run)
                    )
                    await db.commit()
        finally:
            await checkpointer.shutdown_checkpointer()
            await engine.dispose()


async def save_model_boundary(fence: RunFence) -> None:
    """Stop a real graph after it commits the input, before the model can emit anything."""
    entered = asyncio.Event()
    never = asyncio.Event()

    async def model(state: MessagesState) -> MessagesState:
        assert state["messages"]
        entered.set()
        await never.wait()
        return {"messages": []}

    graph = StateGraph(MessagesState)
    graph.add_node("model", model)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    compiled = graph.compile(checkpointer=checkpointer.get_checkpointer())
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(fence.conversation_id),
            "moldy_run_id": str(fence.run_id),
            **checkpoint_tenant(fence.tenant.tenant_id),
        },
    }
    message = HumanMessage(
        content="local read", id=str(uuid.uuid5(fence.run_id, "chat-recovery-input"))
    )
    task = asyncio.create_task(
        compiled.ainvoke({"messages": [message]}, config=config, durability="sync")
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
