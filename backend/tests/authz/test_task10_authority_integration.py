"""Native PostgreSQL authority barriers against an exact archived-org lease (I23).

The leaf permit releases its canonical locks before the leaf I/O, so while a
provider send is parked the real archive endpoint and one authority revocation
(tenant suspension, company-member suspension/removal, organization-only
member suspension/removal) both commit without waiting; the in-flight send
completes and the very next tool, child-launch or provider dispatch is refused
with the exact code even though the lease would bypass the archive.
Participant expansion after the marker never launches. Tenant suspension
freezes the run's terminal fields and mutates no trigger or deployment row
(parent Task 13 owns later API/race revalidation).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import inspect

from app.agent_api import service as api_service
from app.agent_runtime.agent_stream_runner import execute_agent_stream
from app.agent_runtime.identity import make_agent_runtime_name
from app.models.agent import Agent
from app.models.agent_api import AgentDeployment
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.schemas.agent_api import AgentDeploymentCreate
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.task10_runtime_barriers import (
    MUTATIONS,
    Barriers,
    Gate,
    Hook,
    finish,
    install_chat,
    tool_call,
)

if TYPE_CHECKING:
    from tests.authz.task10_native_fixture import Lane

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.task10_native_fixture"]
PAYLOAD = [{"role": "user", "content": "hi"}]


async def _chat(lane: Lane, tmp_path: Path) -> ekit.Harness:
    async with lane.sessions() as db:
        harness = await ekit.setup(db, tmp_path, lane.monkeypatch, [])
    lane.rebind()
    lane.track(harness.seeded.run_id, leased=True)
    return harness


async def _run(harness: ekit.Harness) -> Any:
    cfg = await ekit.entry_cfg(harness)
    return await finish(
        ekit.run_through_worker(harness, harness.seeded.run_id, cfg, PAYLOAD, execute_agent_stream)
    )


async def _mutate(lane: Lane, harness: ekit.Harness, mutation: str) -> None:
    async with lane.sessions() as db:
        await db.execute(MUTATIONS[mutation][0](harness.seeded.graph.context))
        await db.commit()


def _first(harness: ekit.Harness, dispatch: str) -> AIMessage:
    if dispatch == "tool":
        return tool_call("ls", {"path": "/"}, "ls-1")
    return kit.task_call(harness.seeded.child_name, "task-1")


@pytest.mark.parametrize("mutation", list(MUTATIONS))
@pytest.mark.parametrize("dispatch", ["tool", "child", "provider"])
async def test_native_revocation_commits_during_parked_leaf_and_wins_next_dispatch(
    task10_lane: Lane, tmp_path: Path, mutation: str, dispatch: str
) -> None:
    harness = await _chat(task10_lane, tmp_path)
    root, child = harness.seeded.root_model, harness.seeded.child_model
    parked = child if dispatch == "provider" else root
    admin = await task10_lane.admin(harness.seeded.graph.context)
    gate = Gate()
    factory = Barriers(
        {root: [_first(harness, dispatch), AIMessage("done")], child: [AIMessage("helped")]},
        gates={parked: gate},
    )
    install_chat(task10_lane.monkeypatch, harness, factory)

    run = asyncio.create_task(_run(harness))
    await gate.wait_entered()
    # Both commit while the leaf is parked. Archive first: it is a company-admin route, so a
    # suspension committed before it would (correctly) refuse it with 403 tenant_suspended.
    archived = await finish(task10_lane.lifecycle(admin, "archive"))
    await finish(_mutate(task10_lane, harness, mutation))
    assert archived.status_code == 200
    assert not run.done()  # the parked send holds no lock and is still in flight
    gate.release.set()
    row = await finish(run)

    assert (row.status, row.is_active, row.error_code, row.error_message) == (
        "failed",
        False,
        MUTATIONS[mutation][1],
        MUTATIONS[mutation][1],
    )
    assert ([root, child] if dispatch == "provider" else [root]) == kit.SENDS
    assert harness.prepare_agent_calls == 0 and harness.resolutions == [(False, True)]
    assert set(factory.closes().values()) == {1}


async def test_native_participant_expansion_after_archive_never_launches(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness = await _chat(task10_lane, tmp_path)
    seeded = harness.seeded
    context, root = seeded.graph.context, seeded.root_model
    admin = await task10_lane.admin(context)
    newcomer = uuid4()

    async def expand() -> None:
        assert (await task10_lane.lifecycle(admin, "archive")).status_code == 200
        async with task10_lane.sessions() as db:
            parent = await db.get(Agent, seeded.graph.agent_id)
            assert parent is not None
            db.add(
                Agent(
                    id=newcomer,
                    user_id=context.user_id,
                    org_id=context.org_id,
                    tenant_id=context.tenant_id,
                    name="Newcomer",
                    model_id=parent.model_id,
                    system_prompt="late",
                )
            )
            await db.flush()
            db.add(AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=newcomer))
            await db.commit()

    late = make_agent_runtime_name(newcomer)
    script = [tool_call("ls", {"path": "/"}, "ls-1"), kit.task_call(late, "task-late")]
    factory = Barriers(
        {root: [*script, AIMessage("done")], seeded.child_model: []}, hooks={root: expand}
    )
    install_chat(task10_lane.monkeypatch, harness, factory)

    row = await _run(harness)

    assert (row.status, row.error_code) == ("completed", None), row.error_message
    assert [root, root, root] == kit.SENDS and harness.prepare_agent_calls == 0
    assert all(late not in path for path in harness.leases[-1].binding.participant_paths)
    (refused,) = [
        m for m in ekit.SEEN[-1] if isinstance(m, ToolMessage) and m.tool_call_id == "task-late"
    ]
    assert "does not exist" in str(refused.content)


def _columns(row: Any) -> dict[str, Any]:
    return {attr.key: getattr(row, attr.key) for attr in inspect(row).mapper.column_attrs}


async def _entry_rows(lane: Lane, trigger_id: Any, deployment_id: Any) -> tuple[dict, dict]:
    async with lane.sessions() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        deployment = await db.get(AgentDeployment, deployment_id)
        assert trigger is not None and deployment is not None
        return _columns(trigger), _columns(deployment)


async def test_native_tenant_suspension_freezes_terminal_fields_and_entry_rows(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness = await _chat(task10_lane, tmp_path)
    seeded = harness.seeded
    ctx, root = seeded.graph.context, seeded.root_model
    async with task10_lane.sessions() as db:
        trigger = AgentTrigger(
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            agent_id=seeded.graph.agent_id,
            user_id=ctx.user_id,
            trigger_type="interval",
            schedule_config={"interval_minutes": 10},
            input_message="scheduled",
            status="active",
            run_count=0,
        )
        db.add(trigger)
        await db.commit()
        deployment = await api_service.create_deployment(
            db, ctx, AgentDeploymentCreate(agent_id=seeded.graph.agent_id)
        )
        await db.commit()
        ids = (trigger.id, deployment.id)
    before = await _entry_rows(task10_lane, *ids)

    suspend: Hook = lambda: _mutate(task10_lane, harness, "tenant_suspended")  # noqa: E731
    script = [tool_call("ls", {"path": "/"}, "ls-1"), AIMessage("done")]
    factory = Barriers({root: script, seeded.child_model: []}, hooks={root: suspend})
    install_chat(task10_lane.monkeypatch, harness, factory)
    row = await _run(harness)

    frozen = (row.status, row.is_active, row.error_code, row.error_message)
    assert frozen == ("failed", False, "tenant_suspended", "tenant_suspended")
    assert row.completed_at is not None and row.started_at is not None
    assert row.runtime_materialized_at is not None
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert [root] == kit.SENDS
    assert await _entry_rows(task10_lane, *ids) == before
    calls = task10_lane.scheduler_calls
    assert all(calls[name] == [] for name in calls)
