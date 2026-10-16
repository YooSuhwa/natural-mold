"""Native PostgreSQL Agent API + scheduled-trigger barriers for the prepared lease (I23).

Agent API: a parked provider permit blocks the real archive endpoint; the
exact lease then succeeds and projects ``succeeded`` while archive disables
the deployment; archive-first fails the accepted unmaterialized run and its
envelope exactly once with zero provider calls. Trigger: the dedicated
claim-first versus archive-first two-winner race — claim-first creates one
materialized run that finishes (its resolved-external MCP call goes through
the archived lease), archive-first pauses the trigger and creates nothing,
and restore never replays or re-enables it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.agent_api import runtime_service
from app.models.agent_api import AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.conversation_run import ConversationRun
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_trigger_testkit as tkit
from tests.authz.task10_runtime_barriers import (
    Barriers,
    Gate,
    assert_blocked,
    finish,
    install_api,
    install_trigger,
)

if TYPE_CHECKING:
    from tests.authz.task10_native_fixture import Lane

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.task10_native_fixture"]


async def _api(lane: Lane, tmp_path: Path) -> apikit.ApiRun:
    apikit.enable(lane.monkeypatch)
    async with lane.sessions() as db:
        api = await apikit.seed_api_run(db, tmp_path, lane.monkeypatch, with_child=False)
    apikit.forbid_legacy(lane.monkeypatch)
    lane.rebind()
    lane.track(api.execution.conversation_run_id)
    return api


async def _deployment_status(lane: Lane, agent_id: Any) -> str:
    async with lane.sessions() as db:
        row = await db.scalar(select(AgentDeployment).where(AgentDeployment.agent_id == agent_id))
    assert row is not None
    return row.status


async def test_native_agent_api_lease_first_permit_blocks_archive_then_succeeds(
    task10_lane: Lane, tmp_path: Path
) -> None:
    api = await _api(task10_lane, tmp_path)
    root, gate = api.seeded.root_model, Gate()
    factory = Barriers({root: [AIMessage("done")]}, gates={root: gate})
    install_api(task10_lane.monkeypatch, factory, api.server)
    admin = await task10_lane.admin(api.seeded.graph.context)

    run = asyncio.create_task(runtime_service.execute_owned_wait(api.execution))
    await gate.wait_entered()
    archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
    await assert_blocked(archive)
    gate.release.set()
    output, response = await finish(run), await finish(archive)

    assert output == "done" and response.status_code == 200
    assert [root] == kit.SENDS and api.server.discoveries == 1
    state = await apikit.rows(api)
    assert (state.run.status, state.run.error_code) == ("completed", None)
    assert state.run.runtime_materialization_sha256
    assert [envelope.status for envelope in state.envelopes] == ["succeeded"]
    assert await _deployment_status(task10_lane, api.seeded.graph.agent_id) == "disabled"
    assert set(factory.closes().values()) == {1}
    task10_lane.track(state.run.id, leased=True)


async def test_native_agent_api_archive_first_fails_envelope_exactly_once(
    task10_lane: Lane, tmp_path: Path
) -> None:
    api = await _api(task10_lane, tmp_path)
    admin = await task10_lane.admin(api.seeded.graph.context)
    assert (await task10_lane.lifecycle(admin, "archive")).status_code == 200
    archived = await apikit.rows(api)

    with pytest.raises(Exception) as denied:  # noqa: PT011 - opaque gateway refusal
        await runtime_service.execute_owned_wait(api.execution)

    assert "sk-root" not in str(denied.value)
    assert kit.SENDS == [] and api.server.discoveries == 0
    state = await apikit.rows(api)
    assert (state.run.status, state.run.is_active, state.run.error_code) == (
        "failed",
        False,
        "organization_archived",
    )
    assert state.run.runtime_materialized_at is None
    assert [(e.status, e.error_code) for e in state.envelopes] == [
        ("failed", "organization_archived")
    ]
    assert state.envelopes[0].finished_at == archived.envelopes[0].finished_at
    assert state.envelopes[0].conversation_run_id == state.run.id


async def _trigger(lane: Lane, tmp_path: Path) -> tuple[tkit.SeededTrigger, tkit.Harness, Gate]:
    async with lane.sessions() as db:
        seeded = await tkit.seed_trigger(db)
    scripts = {seeded.root_model: [tkit.mcp_call("m1"), AIMessage("trigger done")]}
    harness = tkit.install(lane.monkeypatch, tmp_path, seeded, scripts)
    gate = Gate()
    factory = Barriers(scripts, gates={seeded.root_model: gate})
    install_trigger(lane.monkeypatch, factory, harness.server)
    lane.rebind()
    return seeded, harness, gate


async def _trigger_row(lane: Lane, trigger_id: Any) -> AgentTrigger:
    async with lane.sessions() as db:
        row = await db.get(AgentTrigger, trigger_id)
    assert row is not None
    return row


async def test_native_trigger_claim_first_finishes_through_archived_lease(
    task10_lane: Lane, tmp_path: Path
) -> None:
    seeded, harness, gate = await _trigger(task10_lane, tmp_path)
    admin = await task10_lane.admin(seeded.graph.context)

    claim = asyncio.create_task(tkit.execute(seeded))
    await gate.wait_entered()
    archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
    await assert_blocked(archive)
    gate.release.set()
    await finish(claim)
    assert (await finish(archive)).status_code == 200

    (run,) = await tkit.trigger_runs(seeded)
    assert (run.status, run.output_preview, run.error_message) == ("success", "trigger done", None)
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert conversation_run.status == "completed" and conversation_run.runtime_materialized_at
    task10_lane.track(conversation_run.id, leased=True)
    # The MCP call (resolved_external) ran after archive through the exact lease only.
    assert harness.server.discoveries == 1 and harness.server.calls == 1
    assert [seeded.root_model, seeded.root_model] == kit.SENDS
    assert harness.hydrations == [] and harness.legacy_invokes == []
    trigger = await _trigger_row(task10_lane, seeded.trigger_id)
    assert (trigger.status, trigger.run_count) == ("paused", 1)
    assert task10_lane.scheduler_calls["remove_trigger_job"] == [(seeded.trigger_id,)]


async def test_native_trigger_archive_first_creates_nothing_and_restore_never_replays(
    task10_lane: Lane, tmp_path: Path
) -> None:
    seeded, harness, _gate = await _trigger(task10_lane, tmp_path)
    admin = await task10_lane.admin(seeded.graph.context)
    archived = await task10_lane.lifecycle(admin, "archive")
    assert archived.status_code == 200

    assert await tkit.execute(seeded) is None
    assert await tkit.trigger_runs(seeded) == []
    restored = await task10_lane.lifecycle(admin, "restore", version=archived.headers["etag"])
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    skipped = await tkit.execute(seeded)

    assert skipped is None or skipped.status == "skipped"
    assert all(run.conversation_run_id is None for run in await tkit.trigger_runs(seeded))
    async with task10_lane.sessions() as db:
        runs = await db.scalar(
            select(func.count())
            .select_from(ConversationRun)
            .where(ConversationRun.agent_id == seeded.graph.agent_id)
        )
    assert runs == 0
    trigger = await _trigger_row(task10_lane, seeded.trigger_id)
    assert (trigger.status, trigger.run_count) == ("paused", 0)
    assert kit.SENDS == [] and harness.server.discoveries == 0
    calls = task10_lane.scheduler_calls
    assert calls["add_trigger_job"] == calls["resume_trigger_job"] == []
