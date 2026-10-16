"""I20: seeded rows -> adapter -> materialize -> dispatch root -> prepared child -> terminal."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import agent_stream_runner
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_dispatch import handle_for, terminalize_prepared_run
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.prepared_task_tool_handler import TERMINALIZED
from app.models.organization import OrganizationMember
from app.services import agent_execution_runtime
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.test_prepared_runtime_installed_compiler import PostMarkerSpies
from tests.conftest import TestSession


def _delegating(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
    return {
        seeded.root_model: [kit.task_call(seeded.child_name, "call-t"), AIMessage("done")],
        seeded.child_model: [AIMessage("helped")],
    }


def _forbid_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("post-marker preparation")

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", forbidden)


def _assert_terminal(active: akit.Activated) -> None:
    run_id = active.seeded.run_id
    assert get_prepared_run_registry().state(run_id) is None
    assert get_child_launch_ledger().is_closed(run_id)
    assert set(active.factory.closes().values()) == {1}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stream", "invoke"])
async def test_prepared_root_delegates_to_prepared_child(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    sessions = akit.CountingSessions(TestSession)
    monkeypatch.setattr(agent_execution_runtime, "async_session", sessions)
    active = await akit.activate(db, tmp_path, monkeypatch, _delegating)
    seeded, lease = active.seeded, active.lease
    open_at_send: list[tuple[str, int]] = []

    class Sends(list[str]):
        def append(self, tag: str) -> None:
            open_at_send.append((tag, sessions.open))
            super().append(tag)

    monkeypatch.setattr(kit, "SENDS", Sends())
    _forbid_preparation(monkeypatch)
    spies = PostMarkerSpies(monkeypatch)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    if mode == "stream":
        chunks = [
            c async for c in agent_stream_runner._run_prepared_agent_stream(cfg, handle_for(lease))
        ]
        assert any("done" in chunk for chunk in chunks)
    else:
        text = await agent_stream_runner.execute_prepared_agent_invoke(cfg, handle_for(lease))
        assert text == "done"

    root, child = seeded.root_model, seeded.child_model
    assert [root, child, root] == kit.SENDS
    # Each send holds only its own permit: no outer task-admission lock overlaps.
    assert open_at_send == [(root, 1), (child, 1), (root, 1)]
    assert spies.compiled == [] and "task" not in spies.executed
    _assert_terminal(active)


class _RevokingModel(kit.GuardedScriptedModel):
    """Root model that removes the owner org membership after its first send."""

    _revoke: Any = PrivateAttr(default=None)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        result = await super()._agenerate(*args, **kwargs)
        if self._revoke is not None:
            revoke, self._revoke = self._revoke, None
            await revoke()
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["removed_member", "unprepared_child"])
async def test_removed_member_or_unprepared_child_is_denied(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    def scripts(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
        target = seeded.child_name if case == "removed_member" else "ghost"
        return {
            seeded.root_model: [kit.task_call(target, "call-t"), AIMessage("done")],
            seeded.child_model: [AIMessage("helped")],
        }

    holder: dict[str, Any] = {}

    class Factory(kit.ScriptedFactory):
        def __call__(self, spec: Any, guard: Any) -> kit.FakeOwned:
            owned = super().__call__(spec, guard)
            if case == "removed_member" and spec.model_name == holder["root"]:
                model = _RevokingModel(responses=list(self.scripts[spec.model_name]))
                model._guard, model._tag, model._revoke = guard, spec.model_name, holder["revoke"]
                owned = self.built[spec.model_name] = kit.FakeOwned(model)
            return owned

    def factory(built: dict[str, list[AIMessage]]) -> kit.ScriptedFactory:
        holder["root"] = next(name for name in built if name.startswith("root-"))
        return Factory(built)

    async def revoke() -> None:
        ctx = holder["seeded"].graph.context
        async with TestSession() as tx:
            await tx.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == ctx.org_id,
                    OrganizationMember.user_id == ctx.user_id,
                )
            )
            await tx.commit()

    holder["revoke"] = revoke
    active = await akit.activate(db, tmp_path, monkeypatch, scripts, factory)
    holder["seeded"] = active.seeded
    _forbid_preparation(monkeypatch)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    root = active.seeded.root_model
    if case == "removed_member":
        with pytest.raises(DispatchDeniedError) as denied:
            await agent_stream_runner.execute_prepared_agent_invoke(cfg, handle_for(active.lease))
        assert denied.value.code == "forbidden"
        assert [root] == kit.SENDS
    else:
        text = await agent_stream_runner.execute_prepared_agent_invoke(
            cfg, handle_for(active.lease)
        )
        assert text == "done" and [root, root] == kit.SENDS
    _assert_terminal(active)


class _BlockingChild(kit.GuardedScriptedModel):
    """Child model that parks (holding no permit) until cancelled."""

    _probe: Any = PrivateAttr(default=None)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        await self._probe()
        return await super()._agenerate(*args, **kwargs)


@pytest.mark.asyncio
async def test_terminalization_after_launch_cancels_once_without_lock_overlap(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = akit.CountingSessions(TestSession)
    monkeypatch.setattr(agent_execution_runtime, "async_session", sessions)
    started, cancels, open_in_child = asyncio.Event(), [], []

    async def probe() -> None:
        open_in_child.append(sessions.open)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancels.append(1)
            raise

    class Factory(kit.ScriptedFactory):
        def __call__(self, spec: Any, guard: Any) -> kit.FakeOwned:
            owned = super().__call__(spec, guard)
            if spec.model_name.startswith("child-"):
                model = _BlockingChild(responses=list(self.scripts[spec.model_name]))
                model._guard, model._tag, model._probe = guard, spec.model_name, probe
                owned = self.built[spec.model_name] = kit.FakeOwned(model)
            return owned

    active = await akit.activate(db, tmp_path, monkeypatch, _delegating, Factory)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    running = asyncio.ensure_future(
        agent_stream_runner.execute_prepared_agent_invoke(cfg, handle_for(active.lease))
    )
    await asyncio.wait_for(started.wait(), 10)
    assert open_in_child == [0]
    assert await terminalize_prepared_run(active.lease) == "closed"
    with pytest.raises(DispatchDeniedError) as terminal:
        await asyncio.wait_for(running, 10)
    assert terminal.value.code == TERMINALIZED
    assert cancels == [1] and [active.seeded.root_model] == kit.SENDS
    assert get_child_launch_ledger().terminalize(active.seeded.run_id) == 0
    _assert_terminal(active)
