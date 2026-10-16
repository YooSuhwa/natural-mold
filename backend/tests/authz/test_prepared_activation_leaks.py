"""I24 H4: a failure after commit+activation but before the claim never leaks an ACTIVE lease.

Each entry (chat/queue ``materialize_prepared_execution``, the chat worker, the
API helper, the trigger entry) is cancelled right after activation, i.e. at
the materialization session close; the lease must be terminalized and the
marked run durably failed with ``prepared_run_activation_failed``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import prepared_execution, runtime_service
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.prepared_run_transaction import ACTIVATION_FAILED
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_runtime, prepared_trigger_entry
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_trigger_testkit as tkit
from tests.conftest import TestSession


def _cancel_after_activation(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[Any]:
    """Wrap ``module.materialize_and_activate``: activate, then cancel at the close."""

    activated: list[Any] = []
    original = module.materialize_and_activate

    async def cancelled(*args: Any, **kwargs: Any) -> Any:
        lease = await original(*args, **kwargs)
        activated.append(lease)
        assert get_prepared_run_registry().state(lease.binding.run_id) is not None
        raise asyncio.CancelledError

    monkeypatch.setattr(module, "materialize_and_activate", cancelled)
    return activated


async def _row(run_id: Any) -> ConversationRun:
    async with TestSession() as check:
        row = await check.get(ConversationRun, run_id)
    assert row is not None
    return row


def _assert_abandoned(row: ConversationRun) -> None:
    assert (row.status, row.is_active, row.error_code) == ("failed", False, ACTIVATION_FAILED)
    assert row.runtime_materialized_at is not None
    assert get_prepared_run_registry().state(row.id) is None


async def test_shared_chat_queue_entry_terminalizes_lease_cancelled_after_activation(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.fresh_registry(monkeypatch)
    queued = await kit.queued_run(db)
    inputs = kit.inputs(tmp_path, thread_id=str(queued.graph.conversation_ids[0]))
    factory = kit.ScriptedFactory({"root": [AIMessage("done")]})
    activated = _cancel_after_activation(monkeypatch, agent_execution_runtime)

    async def schedule(_lease: Any) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        await agent_execution_runtime.materialize_prepared_execution(
            queued.request,
            prepare_inputs=kit.resolver(inputs),
            schedule=schedule,
            seams=kit.seams(factory),
        )

    assert len(activated) == 1 and kit.SENDS == []
    _assert_abandoned(await _row(queued.run_id))


async def test_chat_worker_cancelled_after_activation_leaves_no_active_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("never")])
    activated = _cancel_after_activation(monkeypatch, agent_execution_runtime)
    cfg = await ekit.entry_cfg(harness)
    payload = [{"role": "user", "content": "hi"}]

    # The worker absorbs the cancellation (canceled path); the lease must not survive it.
    row = await ekit.run_through_worker(
        harness, harness.seeded.run_id, cfg, payload, execute_agent_stream
    )

    assert len(activated) == 1 and kit.SENDS == []
    _assert_abandoned(row)


async def test_api_entry_cancelled_after_activation_terminalizes_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    activated = _cancel_after_activation(monkeypatch, agent_execution_runtime)

    with pytest.raises(asyncio.CancelledError):
        await runtime_service.execute_owned_wait(api.execution)

    assert len(activated) == 1 and kit.SENDS == []
    state = await apikit.rows(api)
    _assert_abandoned(state.run)
    assert [envelope.status for envelope in state.envelopes] == ["failed"]


async def test_api_missing_execution_metadata_after_activation_terminalizes_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)

    class NoMetadata(prepared_execution.ApiPreparedRunResolver):
        @property
        def execution_cfg(self) -> Any:
            return None

    monkeypatch.setattr(prepared_execution, "ApiPreparedRunResolver", NoMetadata)

    with pytest.raises(prepared_execution.PreparedApiRunError) as missing:
        await runtime_service.execute_owned_wait(api.execution)

    assert missing.value.code == prepared_execution.METADATA_MISSING and kit.SENDS == []
    _assert_abandoned((await apikit.rows(api)).run)


async def test_trigger_entry_cancelled_after_activation_terminalizes_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    tkit.install(monkeypatch, tmp_path, seeded, {seeded.root_model: [AIMessage("never")]})
    activated = _cancel_after_activation(monkeypatch, prepared_trigger_entry)

    with pytest.raises(asyncio.CancelledError):
        await tkit.execute(seeded)

    assert len(activated) == 1 and kit.SENDS == []
    run_id = activated[0].binding.run_id
    _assert_abandoned(await _row(run_id))
    assert not (tmp_path / "runs" / str(run_id)).exists()
