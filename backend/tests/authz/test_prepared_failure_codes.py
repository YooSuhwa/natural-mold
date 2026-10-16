"""I24 M1/M2/M3/M4 for the chat worker and trigger entries.

* M2: a mid-run dispatch denial is the run ``error_code`` (not ``stream_error``
  / ``runtime_error``) in chat and trigger; the trigger envelope inherits it.
* M1: the chat artifact recorder indexes the lease output root, which is
  removed after terminalization.
* M3: with the flag on, the legacy fallback's ``AgentIdentityError`` still
  records the trigger run ``failed`` with its message.
* M4: prepared API/trigger supervision renews ``heartbeat_at``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.agent_runtime.agent_stream_runner import execute_agent_stream
from app.agent_runtime.identity import AgentIdentityError
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_run_input_policy import PreparedRunUnsupportedError
from app.models.conversation_run import ConversationRun
from app.services import (
    agent_execution_runtime,
    agent_execution_supervision,
    conversation_run_worker,
    conversation_stream_service,
    prepared_trigger_entry,
)
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_trigger_testkit as tkit
from tests.authz.agent_execution_runtime_fixture import runtime_request, seed_runtime_run
from tests.conftest import TestSession


def _deny_every_send(monkeypatch: pytest.MonkeyPatch) -> None:
    async def denied(*_a: Any, **_k: Any) -> Any:
        raise DispatchDeniedError("organization_archived")

    monkeypatch.setattr(kit.GuardedScriptedModel, "_agenerate", denied)


@pytest.mark.asyncio
async def test_chat_mid_run_denial_code_and_lease_output_root(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("never")])
    _deny_every_send(monkeypatch)
    dirs: list[Any] = []
    original = conversation_stream_service.build_artifact_recorder

    def spy(**kwargs: Any) -> Any:
        dirs.append(kwargs.get("output_dir"))
        return original(**kwargs)

    monkeypatch.setattr(conversation_stream_service, "build_artifact_recorder", spy)
    cfg = await ekit.entry_cfg(harness)
    run_id = harness.seeded.run_id

    row = await ekit.run_through_worker(
        harness, run_id, cfg, [{"role": "user", "content": "hi"}], execute_agent_stream
    )

    assert (row.status, row.error_code) == ("failed", "organization_archived")
    root = tmp_path / "prepared-runs" / str(run_id)
    assert dirs == [root / "output"]
    assert not root.exists()


@pytest.mark.asyncio
async def test_trigger_mid_run_denial_code_is_shared_by_run_and_envelope(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    tkit.install(monkeypatch, tmp_path, seeded, {seeded.root_model: [AIMessage("never")]})
    _deny_every_send(monkeypatch)

    await tkit.execute(seeded)

    (envelope,) = await tkit.trigger_runs(seeded)
    assert (envelope.status, envelope.error_message) == ("failed", "organization_archived")
    run = await tkit.conversation_run(envelope.conversation_run_id)
    assert (run.status, run.error_code) == ("failed", "organization_archived")
    assert not (tmp_path / "runs" / str(run.id)).exists()


@pytest.mark.asyncio
async def test_flag_on_legacy_fallback_identity_error_records_failed_trigger_run(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    tkit.install(monkeypatch, tmp_path, seeded, {seeded.root_model: [AIMessage("never")]})

    async def unsupported(_target: Any) -> Any:
        raise PreparedRunUnsupportedError("memory_tools")

    async def blocked(_build: Any) -> None:
        raise AgentIdentityError("subagent identity blocked")

    monkeypatch.setattr(prepared_trigger_entry, "_prepare", unsupported)
    monkeypatch.setattr(trigger_executor, "_configure_trigger_subagents", blocked)

    await tkit.execute(seeded)

    (envelope,) = await tkit.trigger_runs(seeded)
    assert envelope.status == "failed"
    assert envelope.error_message == str(AgentIdentityError("subagent identity blocked"))
    assert envelope.finished_at is not None


@pytest.mark.asyncio
async def test_prepared_supervision_poll_renews_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    run, agent, conversation = await seed_runtime_run()
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    async with TestSession() as tx:
        row = await tx.get(ConversationRun, run.id)
        assert row is not None
        row.status, row.worker_instance_id, row.heartbeat_at = "running", "w-i24", stale
        await tx.commit()
    monkeypatch.setattr(conversation_run_worker, "_heartbeat_interval_seconds", lambda: 0.0)
    owner = agent_execution_runtime._OwnedRuntime(
        runtime_request(run, agent, conversation), "w-i24"
    )

    poll = asyncio.ensure_future(agent_execution_supervision.poll_cancel(owner, True))
    await asyncio.sleep(0.4)
    poll.cancel()

    async with TestSession() as check:
        renewed = await check.get(ConversationRun, run.id)
    assert renewed is not None and renewed.heartbeat_at is not None
    assert renewed.heartbeat_at > stale + timedelta(minutes=30)
