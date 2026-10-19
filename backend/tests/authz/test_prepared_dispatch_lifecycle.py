"""I20 dispatch lifecycle: single owner, no DB-writing hooks, terminalize exactly once."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import agent_stream_runner
from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_dispatch import handle_for, invoke_prepared, prepared_dispatch
from app.agent_runtime.prepared_run_registry import (
    LEASE_MISSING,
    LeaseState,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit


def _done(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
    return {seeded.root_model: [AIMessage("done")], seeded.child_model: []}


class _Hooks:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_pre(self, _ctx: Any) -> None:
        self.calls.append("pre")

    async def run_post(self, _ctx: Any, _result: Any) -> None:
        self.calls.append("post")

    async def run_failure(self, _ctx: Any, _error: Any) -> None:
        self.calls.append("failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stream", "invoke"])
async def test_prepared_runs_skip_db_writing_hooks(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    active = await akit.activate(db, tmp_path, monkeypatch, _done)
    recorder = _Hooks()
    monkeypatch.setattr(agent_stream_runner, "hooks", recorder)
    cfg = active.prepare.execution_cfg
    assert cfg is not None and cfg.user_id
    handle = handle_for(active.lease)
    if mode == "stream":
        assert [c async for c in agent_stream_runner._run_prepared_agent_stream(cfg, handle)]
    else:
        assert await agent_stream_runner.execute_prepared_agent_invoke(cfg, handle) == "done"
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_dispatch_is_single_owner(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = await akit.activate(db, tmp_path, monkeypatch, _done)
    handle, registry = handle_for(active.lease), get_prepared_run_registry()
    async with prepared_dispatch(handle) as lease:
        assert registry.state(lease.binding.run_id) is LeaseState.DISPATCHING
        with pytest.raises(PreparedRunLeaseError) as second:
            await invoke_prepared(handle)
        assert second.value.code == LEASE_MISSING
        # The owner's leaves still get permits while the lease is dispatching.
        result = await lease.runnable.ainvoke(
            {"messages": list(lease.messages)}, dict(lease.config)
        )
    assert result["messages"][-1].content == "done" and [active.seeded.root_model] == kit.SENDS
    assert registry.state(active.seeded.run_id) is None


class _ParkedRoot(kit.GuardedScriptedModel):
    _started: Any = PrivateAttr(default=None)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        self._started.set()
        await asyncio.Event().wait()
        return await super()._agenerate(*args, **kwargs)


def _counting(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts = {"ledger": 0, "registry": 0}
    ledger, registry = get_child_launch_ledger(), get_prepared_run_registry()
    ledger_terminalize, registry_terminalize = ledger.terminalize, registry.terminalize

    def ledger_spy(run_id: Any) -> int:
        counts["ledger"] += 1
        return ledger_terminalize(run_id)

    async def registry_spy(run_id: Any) -> Any:
        counts["registry"] += 1
        return await registry_terminalize(run_id)

    monkeypatch.setattr(ledger, "terminalize", ledger_spy)
    monkeypatch.setattr(registry, "terminalize", registry_spy)
    return counts


@pytest.mark.asyncio
async def test_outer_cancel_terminalizes_ledger_and_registry_once(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class Factory(kit.ScriptedFactory):
        def __call__(self, spec: Any, guard: Any) -> kit.FakeOwned:
            owned = super().__call__(spec, guard)
            if spec.model_name.startswith("root-"):
                model = _ParkedRoot(responses=list(self.scripts[spec.model_name]))
                model._guard, model._tag, model._started = guard, spec.model_name, started
                owned = self.built[spec.model_name] = kit.FakeOwned(model)
            return owned

    active = await akit.activate(db, tmp_path, monkeypatch, _done, Factory)
    counts = _counting(monkeypatch)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    task = asyncio.ensure_future(
        agent_stream_runner.execute_prepared_agent_invoke(cfg, handle_for(active.lease))
    )
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert counts == {"ledger": 1, "registry": 1}
    assert get_prepared_run_registry().state(active.seeded.run_id) is None
    assert get_child_launch_ledger().is_closed(active.seeded.run_id)
    assert set(active.factory.closes().values()) == {1}


@pytest.mark.asyncio
async def test_early_stream_close_terminalizes_ledger_and_registry_once(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = await akit.activate(db, tmp_path, monkeypatch, _done)
    counts = _counting(monkeypatch)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    stream = agent_stream_runner._run_prepared_agent_stream(cfg, handle_for(active.lease))
    assert await anext(stream)
    await stream.aclose()
    assert counts == {"ledger": 1, "registry": 1}
    assert get_prepared_run_registry().state(active.seeded.run_id) is None
    assert get_child_launch_ledger().is_closed(active.seeded.run_id)
    assert set(active.factory.closes().values()) == {1}
