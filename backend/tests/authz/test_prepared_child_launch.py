"""One-shot child-launch admission: fresh authority, one winner, no held locks."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError, open_dispatch_permit
from app.agent_runtime.prepared_child_launch import (
    ChildLaunchLedger,
    ChildLaunchState,
    PreparedChildLaunchAdmission,
    child_launch_descriptors,
)
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.prepared_subagent_catalog import (
    PreparedChildPolicy,
    build_prepared_child_catalog,
    model_identity,
)
from app.agent_runtime.prepared_task_tool_handler import (
    TERMINALIZED,
    PreparedStateProjector,
    run_prepared_child,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.models.organization import OrganizationMember
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.runtime_prototype import ScriptedModel
from tests.conftest import TestSession

LEAF = "tool:leaf"
CHILD = "root/researcher"
MODEL = ScriptedModel(responses=[AIMessage(content="unused")])
CATALOG = build_prepared_child_catalog(
    [{"name": "researcher", "description": "r", "system_prompt": "p", "tools": []}],
    model=MODEL,
    tools=[],
    permissions=None,
    stored_policy={CHILD: PreparedChildPolicy(model_identity(MODEL), frozenset())},
)
ALLOWED = child_launch_descriptors(CATALOG) | {LEAF}


class Sessions:
    """Session factory that counts open authority transactions."""

    def __init__(self) -> None:
        self.open = 0
        self.opened = 0
        self.hold_exit = False
        self.exiting = asyncio.Event()

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        async with TestSession() as session:
            self.open += 1
            self.opened += 1
            try:
                yield session
            finally:
                self.open -= 1
                if self.hold_exit:
                    self.exiting.set()
                    await asyncio.Event().wait()


@pytest.fixture
async def setup(db: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    graph = await seed_graph(db)
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    ctx = graph.context
    binding = PreparedRunBinding(
        ctx.tenant_id,
        ctx.org_id,
        ctx.user_id,
        graph.agent_id,
        graph.conversation_ids[0],
        uuid.uuid4(),
        "thread",
    )
    token = registry.reserve(binding)
    lease = kit.lease(binding, token)
    await registry.stage(lease)
    registry.activate(binding.run_id, token=token, digest=lease.digest)
    sessions, ledger = Sessions(), ChildLaunchLedger()
    admission = PreparedChildLaunchAdmission(
        lease,
        catalog=CATALOG,
        catalog_digest=CATALOG.digest,
        allowed_descriptors=ALLOWED,
        session_factory=sessions,  # type: ignore[arg-type]
        ledger=ledger,
    )
    yield {
        "db": db,
        "graph": graph,
        "registry": registry,
        "lease": lease,
        "sessions": sessions,
        "ledger": ledger,
        "admission": admission,
    }
    reset_prepared_run_registry_for_tests()


async def _reserve(admission: PreparedChildLaunchAdmission, invocation: str = "call-1", **kw: Any):
    values = {
        "parent_task_id": "root",
        "parent_path": "root",
        "child_id": CHILD,
        "child_token": CATALOG.children[CHILD].token,
    }
    values.update(kw)
    return await admission.reserve_child_launch(invocation_id=invocation, **values)


@pytest.mark.asyncio
async def test_concurrent_duplicate_reservation_has_exactly_one_winner(setup) -> None:
    results = await asyncio.gather(
        _reserve(setup["admission"]), _reserve(setup["admission"]), return_exceptions=True
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, DispatchDeniedError)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].code == "child_launch_duplicate"
    assert setup["ledger"].state(winners[0].key) is ChildLaunchState.RESERVED
    assert winners[0].key.invocation_id == "call-1" and setup["sessions"].open == 0
    assert not hasattr(winners[0], "session") and not hasattr(winners[0], "proof")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"child_token": "forged"},
        {"parent_path": "root/researcher"},
        {"parent_task_id": ""},
        {"child_id": "root/x"},
    ],
)
async def test_wrong_child_token_or_parent_is_not_prepared(setup, override) -> None:
    with pytest.raises(DispatchDeniedError) as denied:
        await _reserve(setup["admission"], **override)
    assert denied.value.code == "child_launch_not_prepared"
    assert setup["sessions"].opened == 0


@pytest.mark.asyncio
async def test_cancellation_before_launch_releases_the_reservation(setup) -> None:
    sessions: Sessions = setup["sessions"]
    sessions.hold_exit = True
    pending = asyncio.ensure_future(_reserve(setup["admission"]))
    await sessions.exiting.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    sessions.hold_exit = False
    reservation = await _reserve(setup["admission"])
    assert setup["ledger"].state(reservation.key) is ChildLaunchState.RESERVED


@pytest.mark.asyncio
async def test_registry_and_ledger_terminalization_reject_new_reservations(setup) -> None:
    setup["ledger"].terminalize(setup["lease"].binding.run_id)
    with pytest.raises(DispatchDeniedError) as closed:
        await _reserve(setup["admission"])
    assert closed.value.code == "prepared_run_lease_missing"
    other = PreparedChildLaunchAdmission(
        setup["lease"],
        catalog=CATALOG,
        catalog_digest=CATALOG.digest,
        allowed_descriptors=ALLOWED,
        session_factory=TestSession,
        ledger=ChildLaunchLedger(),
    )
    await setup["registry"].terminalize(setup["lease"].binding.run_id)
    with pytest.raises(DispatchDeniedError) as missing:
        await _reserve(other)
    assert missing.value.code == "prepared_run_lease_missing"


@pytest.mark.asyncio
async def test_child_launch_releases_outer_lock_and_revocation_denies_first_leaf(setup) -> None:
    sessions, lease, db = setup["sessions"], setup["lease"], setup["db"]
    seen: dict[str, Any] = {"leaves": 0}

    async def child(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        seen["open_at_child"] = sessions.open
        seen["config"] = config
        async with open_dispatch_permit(
            lease,
            kind="tool",
            descriptor_id=LEAF,
            allowed_descriptors=ALLOWED,
            session_factory=sessions,
        ):
            seen["leaves"] += 1
        return {"messages": []}

    reservation = await _reserve(setup["admission"])
    assert sessions.open == 0
    ctx = setup["graph"].context
    member = await db.get(OrganizationMember, (ctx.org_id, ctx.user_id))
    assert member is not None
    member.status = "suspended"
    await db.commit()

    with pytest.raises(DispatchDeniedError) as denied:
        await run_prepared_child(
            reservation,
            RunnableLambda(child),
            {"messages": []},
            invocation_id="call-1",
            projector=PreparedStateProjector(),
        )
    assert denied.value.code == "forbidden"
    assert seen["open_at_child"] == 0 and seen["leaves"] == 0
    assert not any(isinstance(v, AsyncSession) for v in seen["config"]["configurable"].values())
    assert setup["ledger"].state(reservation.key) is ChildLaunchState.FINISHED
    assert reservation.finish() is False


@pytest.mark.asyncio
async def test_terminalization_and_cancellation_after_launch_clean_up_once(setup) -> None:
    ledger: ChildLaunchLedger = setup["ledger"]
    started, cancels = asyncio.Event(), []

    async def child(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancels.append(1)
            raise
        return state

    async def launch(invocation: str) -> Any:
        reservation = await _reserve(setup["admission"], invocation)
        task = asyncio.ensure_future(
            run_prepared_child(
                reservation,
                RunnableLambda(child),
                {"messages": []},
                invocation_id=invocation,
                projector=PreparedStateProjector(),
            )
        )
        await started.wait()
        started.clear()
        return reservation, task

    first, parent = await launch("call-1")
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert cancels == [1] and ledger.state(first.key) is ChildLaunchState.FINISHED

    second, running = await launch("call-2")
    assert ledger.terminalize(second.key.lease_id) == 1
    with pytest.raises(DispatchDeniedError) as terminal:
        await running
    assert terminal.value.code == TERMINALIZED
    assert cancels == [1, 1] and ledger.terminalize(second.key.lease_id) == 0
    with pytest.raises(DispatchDeniedError):
        await _reserve(setup["admission"], "call-1")
