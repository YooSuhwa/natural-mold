"""Drive real seeded runs through the owned worker with the prepared lease on (I16/I19).

Rows come from ``prepared_adapter_testkit.seed`` (root + stored child + MCP
link). The flag is on, the materializer gets the I15 scripted/permit-guarded
model factory and fake MCP server, and spies record every chat-config
resolution (with whether it ran under the gateway locks or after the marker)
plus any ``_prepare_agent`` call (which must never happen).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import agent_stream_runner, langgraph_agent_stream_runner
from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_model_factory import LeaseModelSpec
from app.agent_runtime.runtime_config import AgentConfig, PreparedRunLease
from app.agent_runtime.runtime_hydration_guard import active_lease_run
from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_proof import live_proof
from app.config import settings
from app.models.conversation_run import ConversationRun
from app.models.memory import UserMemorySettings
from app.services import (
    agent_execution_runtime,
    conversation_run_worker,
    prepared_conversation_executor,
    prepared_conversation_start,
    prepared_run_adapter,
)
from app.services.conversation_stream_service import resolve_agent_context
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession

SEEN: list[list[BaseMessage]] = []


class RecordingModel(kit.GuardedScriptedModel):
    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        SEEN.append(list(messages))
        return await super()._agenerate(messages, stop, run_manager, **kw)


@dataclass
class RecordingFactory(kit.ScriptedFactory):
    def __call__(self, spec: LeaseModelSpec, guard: LeaseSendGuard) -> kit.FakeOwned:
        model = RecordingModel(responses=list(self.scripts[spec.model_name]))
        model._guard, model._tag = guard, spec.model_name
        self.built[spec.model_name] = kit.FakeOwned(model)
        return self.built[spec.model_name]


@dataclass
class Harness:
    seeded: akit.Seeded
    factory: RecordingFactory
    server: kit.FakeMcpServer
    # (after_marker, under_run_lock) per chat-config resolution.
    resolutions: list[tuple[bool, bool]] = field(default_factory=list)
    prepare_agent_calls: int = 0
    leases: list[PreparedRunLease] = field(default_factory=list)

    def script(self, root: list[AIMessage]) -> None:
        """Replace the root model's responses for the next lease built."""

        self.factory.scripts = {**self.factory.scripts, self.seeded.root_model: root}


def enable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded: akit.Seeded, scripts: dict
) -> Harness:
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", True)
    SEEN.clear()
    harness = Harness(seeded, RecordingFactory(scripts), kit.FakeMcpServer())
    seams = kit.seams(harness.factory, harness.server)
    monkeypatch.setattr(prepared_conversation_start, "materializer_seams", lambda: seams)
    root = tmp_path / "prepared-runs"
    for module in (prepared_conversation_start, prepared_conversation_executor):
        monkeypatch.setattr(module, "prepared_run_root", lambda: root)

    async def resolve(db: AsyncSession, *args: Any, **kwargs: Any) -> AgentConfig:
        proof = live_proof(db)
        locked = proof is not None and proof.cursor.position is LockClass.RUN
        harness.resolutions.append((active_lease_run() is not None, locked))
        return await resolve_agent_context(db, *args, **kwargs)

    async def no_prepare(*_a: Any, **_k: Any) -> Any:
        harness.prepare_agent_calls += 1
        raise AssertionError("_prepare_agent must not run for a prepared lease")

    monkeypatch.setattr(prepared_run_adapter, "resolve_agent_context", resolve)
    for runner in (agent_stream_runner, langgraph_agent_stream_runner):
        monkeypatch.setattr(runner, "_prepare_agent", no_prepare)
    materialize = agent_execution_runtime.materialize_prepared_execution

    async def capture(*args: Any, **kwargs: Any) -> PreparedRunLease:
        lease = await materialize(*args, **kwargs)
        harness.leases.append(lease)
        return lease

    monkeypatch.setattr(agent_execution_runtime, "materialize_prepared_execution", capture)
    return harness


async def setup(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_script: list[AIMessage],
    *,
    memory_settings: bool = True,
    **seed_options: Any,
) -> Harness:
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db, **seed_options)
    if memory_settings:
        # Memory read stays on (recalled under the locks); write tools aren't leasable yet.
        user_id = seeded.graph.context.user_id
        db.add(UserMemorySettings(user_id=user_id, memory_write_policy="off"))
        await db.commit()
    scripts = {seeded.root_model: root_script, seeded.child_model: []}
    return enable(monkeypatch, tmp_path, seeded, scripts)


async def entry_cfg(harness: Harness, *, checkpoint_id: str | None = None) -> AgentConfig:
    """The entry-side (pre-acceptance) config the chat routes hand to the worker."""

    seeded = harness.seeded
    async with TestSession() as db:
        cfg = await resolve_agent_context(
            db,
            seeded.request.conversation_id,
            seeded.user,
            context=seeded.graph.context,
            checkpoint_id=checkpoint_id,
            # The legacy child compile lazy-loads ``child.model`` (MissingGreenlet
            # on aiosqlite); the entry cfg is metadata only, the lease
            # prepares children itself under the locks.
            include_subagents=False,
        )
        await db.commit()
    return cfg


async def queued_run(harness: Harness, *, source: str = "chat") -> uuid.UUID:
    seeded = harness.seeded
    async with TestSession() as db:
        run = ConversationRun(
            conversation_id=seeded.request.conversation_id,
            agent_id=seeded.graph.agent_id,
            user_id=seeded.graph.context.user_id,
            source=source,
            status="queued",
            is_active=True,
        )
        db.add(run)
        await db.commit()
        return run.id


async def run_through_worker(
    harness: Harness,
    run_id: uuid.UUID,
    cfg: AgentConfig,
    payload: Any,
    executor: Any,
    **extra: Any,
) -> ConversationRun:
    """``start_conversation_run`` + await the owned task, then reload the row."""

    seeded = harness.seeded
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=seeded.request.conversation_id,
        cfg=cfg,
        user=seeded.user,
        input_payload=payload,
        moldy_source=extra.pop("moldy_source", "chat"),
        executor_fn=executor,
        **extra,
    )
    task = conversation_run_worker.get_run_task_registry().get(run_id)
    assert task is not None
    await task
    async with TestSession() as db:
        row = await db.get(ConversationRun, run_id)
    assert row is not None
    return row
