"""I17 ownership lifecycle: Langfuse run id, local-owner conflict, pre/mid-run cancel."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service
from app.agent_runtime import agent_stream_runner
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.prepared_run_transaction import (
    RUN_NOT_MATERIALIZABLE,
    PreparedRunTransactionError,
)
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_runtime, conversation_run_service
from app.services.conversation_run_worker import get_run_task_registry
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession


async def _request_cancel(api: apikit.ApiRun) -> None:
    async with TestSession() as tx:
        run = await tx.get(ConversationRun, api.execution.conversation_run_id, with_for_update=True)
        assert run is not None
        await conversation_run_service.request_cancel_run(tx, run, reason="stop")
        await tx.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wait", "stream"])
async def test_langfuse_run_id_is_the_public_api_run_id(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, mode=mode)
    apikit.forbid_legacy(monkeypatch)
    seen: list[str] = []
    original = agent_stream_runner.build_langfuse_run_context

    def spy(cfg: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["run_id"])
        return original(cfg, **kwargs)

    monkeypatch.setattr(agent_stream_runner, "build_langfuse_run_context", spy)
    if mode == "wait":
        assert await runtime_service.execute_owned_wait(api.execution) == "done"
    else:
        assert [c async for c in runtime_service.stream_owned_run(api.execution)]
    assert seen == [api.execution.public_run_id]


@pytest.mark.asyncio
async def test_local_owner_conflict_terminalizes_lease_and_fails_run(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    run_id = api.execution.conversation_run_id

    async def owner() -> None:
        await asyncio.Event().wait()

    other = asyncio.ensure_future(owner())
    get_run_task_registry().start(run_id, other)
    try:
        with pytest.raises(agent_execution_runtime.RuntimeOwnershipConflict) as conflict:
            await runtime_service.execute_owned_wait(api.execution)
    finally:
        other.cancel()
    assert conflict.value.status == "locally_owned"
    assert kit.SENDS == [] and get_prepared_run_registry().state(run_id) is None
    assert set(api.factory.closes().values()) == {1}
    state = await apikit.rows(api)
    assert state.run.status == "failed" and state.run.error_code == "prepared_run_locally_owned"
    assert [envelope.status for envelope in state.envelopes] == ["failed"]


@pytest.mark.asyncio
async def test_cancel_before_materialize_acks_canceled_without_marker(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    await _request_cancel(api)

    with pytest.raises(PreparedRunTransactionError) as refused:
        await runtime_service.execute_owned_wait(api.execution)

    assert refused.value.code == RUN_NOT_MATERIALIZABLE
    assert kit.SENDS == [] and api.server.discoveries == 0
    state = await apikit.rows(api)
    assert state.run.status == "canceled" and state.run.runtime_materialized_at is None
    assert [envelope.status for envelope in state.envelopes] == ["canceled"]


class _ParkedRoot(kit.GuardedScriptedModel):
    """Root model that parks after its permit is granted until cancelled."""

    _parked: Any = PrivateAttr(default=None)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        self._parked.set()
        await asyncio.Event().wait()
        return await super()._agenerate(*args, **kwargs)


@pytest.mark.asyncio
async def test_mid_run_cancel_is_observed_by_poll_and_acks_canceled(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parked = asyncio.Event()

    class Factory(kit.ScriptedFactory):
        def __call__(self, spec: Any, guard: Any) -> kit.FakeOwned:
            owned = super().__call__(spec, guard)
            if spec.model_name.startswith("root-"):
                model = _ParkedRoot(responses=[AIMessage("never")])
                model._guard, model._tag, model._parked = guard, spec.model_name, parked
                owned = self.built[spec.model_name] = kit.FakeOwned(model)
            return owned

    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, factory=Factory)
    apikit.forbid_legacy(monkeypatch)
    running = asyncio.ensure_future(runtime_service.execute_owned_wait(api.execution))
    await asyncio.wait_for(parked.wait(), 10)
    await _request_cancel(api)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, 10)

    assert kit.SENDS == []
    state = await apikit.rows(api)
    assert state.run.status == "canceled" and state.run.cancellation_acknowledged_at is not None
    assert [envelope.status for envelope in state.envelopes] == ["canceled"]
    assert get_prepared_run_registry().state(api.execution.conversation_run_id) is None
    assert set(api.factory.closes().values()) == {1}
