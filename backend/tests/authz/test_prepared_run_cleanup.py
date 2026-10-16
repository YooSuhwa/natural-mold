"""Bounded, exactly-once cleanup and fail-closed authority for prepared runs (I15)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import prepared_run_transaction as transaction
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_run_registry import (
    LEASE_MISSING,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.agent_runtime.prepared_run_support import (
    LateBoundAuthority,
    OnceCloser,
    bounded_cleanup,
)
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_runtime
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_once_closer_runs_each_closer_once_under_concurrency() -> None:
    calls: list[str] = []
    gate = asyncio.Event()

    async def slow() -> None:
        calls.append("slow")
        await gate.wait()

    async def failing() -> None:
        calls.append("failing")
        raise OSError("close failed")

    closer = OnceCloser()
    closer.add(slow)
    closer.add(failing)
    waiters = [asyncio.ensure_future(closer()) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*waiters, return_exceptions=True)

    assert calls == ["failing", "slow"]
    assert all(isinstance(item, OSError) for item in results)
    with pytest.raises(RuntimeError):
        closer.add(slow)


@pytest.mark.asyncio
async def test_bounded_cleanup_times_out_and_never_raises() -> None:
    async def hang() -> None:
        await asyncio.sleep(60)

    async def boom() -> None:
        raise ValueError("cleanup failed")

    await bounded_cleanup(hang, label="hang", limit_seconds=0.01)
    await bounded_cleanup(boom, label="boom")


@pytest.mark.asyncio
async def test_late_bound_authority_denies_before_bind() -> None:
    authority = LateBoundAuthority()
    permit = authority.open(
        None,  # type: ignore[arg-type]
        kind="provider",
        descriptor_id="provider:root:primary:openai:root",
        allowed_descriptors=frozenset({"provider:root:primary:openai:root"}),
        session_factory=TestSession,
    )
    with pytest.raises(DispatchDeniedError) as denied:
        async with permit:
            pytest.fail("permit opened before the authority lease was bound")
    assert denied.value.code == LEASE_MISSING


class _HangingServer(kit.FakeMcpServer):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def discover(self, _key: str, _config: dict, _interceptors: list) -> list[BaseTool]:
        self.discoveries += 1
        self.started.set()
        await asyncio.sleep(60)
        return []


async def _materialize(queued: kit.QueuedRun, inputs: Any, seams: Any) -> Any:
    async def schedule(_lease: Any) -> None:
        return None

    return await agent_execution_runtime.materialize_prepared_execution(
        queued.request, prepare_inputs=kit.resolver(inputs), schedule=schedule, seams=seams
    )


@pytest.mark.asyncio
async def test_cancel_during_mcp_discovery_rolls_back_and_closes_transient(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = kit.fresh_registry(monkeypatch)
    queued = await kit.queued_run(db)
    server = _HangingServer()
    closed: list[int] = []

    async def transient() -> None:
        closed.append(1)

    inputs = kit.inputs(
        tmp_path,
        thread_id=str(queued.graph.conversation_ids[0]),
        mcp_configs=(kit.mcp_config(),),
        transient_closers=(transient,),
    )
    factory = kit.ScriptedFactory({"root": [AIMessage("done")]})
    task = asyncio.ensure_future(_materialize(queued, inputs, kit.seams(factory, server)))
    await server.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert server.discoveries == 1 and closed == [1] and factory.built == {}
    assert registry.state(queued.run_id) is None
    async with TestSession() as check:
        row = await check.get(ConversationRun, queued.run_id)
    assert row is not None and row.status == "queued" and row.runtime_materialized_at is None


@pytest.mark.asyncio
async def test_terminalized_lease_cannot_dispatch_or_rebuild(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.fresh_registry(monkeypatch)
    queued = await kit.queued_run(db)
    factory = kit.ScriptedFactory({"root": [AIMessage("done")]})
    inputs = kit.inputs(tmp_path, thread_id=str(queued.graph.conversation_ids[0]))
    lease = await _materialize(queued, inputs, kit.seams(factory))
    registry = get_prepared_run_registry()
    await registry.terminalize(queued.run_id)

    with pytest.raises(DispatchDeniedError, match=LEASE_MISSING):
        await lease.runnable.ainvoke({"messages": list(lease.messages)}, dict(lease.config))
    assert kit.SENDS == []
    with pytest.raises(PreparedRunLeaseError, match=LEASE_MISSING):
        registry.acquire(lease.binding, token=lease.token, digest=lease.digest)


def test_i15_modules_have_no_i20_dispatch_edge() -> None:
    root = Path(transaction.__file__).parent
    names = ("compile", "inputs", "materializer", "participants", "support", "transaction")
    for name in names:
        assert "prepared_run_dispatch" not in (root / f"prepared_run_{name}.py").read_text()
