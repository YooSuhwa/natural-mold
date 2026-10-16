"""D-06: trigger routes stay PRIVATE and re-check agent#can_run (plan 9.1, 11.6)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from app.agent_runtime.trigger_execution_claim import claim_trigger_run
from app.authz import runtime
from app.authz.runtime import get_authz
from app.authz.service import AuthzService, ShadowSample, Verdict
from app.authz.trigger_scope import require_trigger_actor
from app.exceptions import AppError
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from tests.authz.authz_service_testkit import RecordingClient
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_triggers import _create_agent, _create_model

USER = f"user:{TEST_USER_ID}"
TRIGGER = {
    "trigger_type": "interval",
    "schedule_config": {"interval_minutes": 10},
    "input_message": "run",
}


def _service(
    mode: str,
    allowed: Iterable[tuple[str, str, str]] = (),
    *,
    samples: list[ShadowSample] | None = None,
) -> AuthzService:
    hook = samples.append if samples is not None else None
    client = RecordingClient(allowed=frozenset(allowed))
    return AuthzService(client, mode=mode, shadow_hook=hook)  # type: ignore[arg-type]


def _install(app: FastAPI, service: AuthzService) -> None:
    app.dependency_overrides[get_authz] = lambda: service


def _code(response) -> str:
    return response.json()["error"]["code"]


async def _agent_with_trigger(client: AsyncClient) -> tuple[str, str]:
    agent_id = await _create_agent(client, await _create_model(client))
    created = await client.post(f"/api/agents/{agent_id}/triggers", json=TRIGGER)
    assert created.status_code == 201
    return agent_id, created.json()["id"]


@pytest.mark.asyncio
async def test_trigger_writes_require_agent_can_run(client: AsyncClient, test_app) -> None:
    agent_id, trigger_id = await _agent_with_trigger(client)
    try:
        _install(test_app, _service("enforce"))
        denied = await client.post(f"/api/agents/{agent_id}/triggers", json=TRIGGER)
        assert denied.status_code == 404 and _code(denied) == "AGENT_NOT_FOUND"
        update = await client.patch(f"/api/triggers/{trigger_id}", json={"status": "paused"})
        assert update.status_code == 404 and _code(update) == "AGENT_NOT_FOUND"
        put = await client.put(
            f"/api/agents/{agent_id}/triggers/{trigger_id}", json={"status": "paused"}
        )
        assert put.status_code == 404
        run_now = await client.post(f"/api/triggers/{trigger_id}/run-now")
        assert run_now.status_code == 404 and _code(run_now) == "AGENT_NOT_FOUND"
        listed = await client.get("/api/triggers")  # PRIVATE reads need no agent decision
        assert [row["id"] for row in listed.json()] == [trigger_id]
        assert listed.json()[0]["status"] == "active"  # denied updates never wrote
        _install(test_app, _service("enforce", {(USER, "can_run", f"agent:{agent_id}")}))
        assert (await client.post(f"/api/agents/{agent_id}/triggers", json=TRIGGER)).status_code
        ok = await client.patch(f"/api/triggers/{trigger_id}", json={"status": "paused"})
        assert ok.status_code == 200 and ok.json()["status"] == "paused"
    finally:
        test_app.dependency_overrides.pop(get_authz, None)


@pytest.mark.asyncio
async def test_trigger_shadow_samples_legacy_allow(client: AsyncClient, test_app) -> None:
    agent_id = await _create_agent(client, await _create_model(client))
    samples: list[ShadowSample] = []
    try:
        _install(test_app, _service("shadow", samples=samples))
        created = await client.post(f"/api/agents/{agent_id}/triggers", json=TRIGGER)
    finally:
        test_app.dependency_overrides.pop(get_authz, None)
    assert created.status_code == 201
    assert [(s.permission, s.obj, s.legacy) for s in samples] == [
        ("can_run", f"agent:{agent_id}", Verdict.ALLOW)
    ]


@pytest.mark.asyncio
async def test_fire_time_requires_owner_can_run(client: AsyncClient, monkeypatch) -> None:
    agent_id, trigger_id = await _agent_with_trigger(client)
    trigger_uuid = uuid.UUID(trigger_id)
    monkeypatch.setattr(runtime, "_current", _service("enforce"))
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_uuid)
        assert trigger is not None
        with pytest.raises(AppError) as denied:
            await require_trigger_actor(db, trigger)
        assert denied.value.code == "AGENT_NOT_FOUND"
        assert await claim_trigger_run(db, trigger_id, trigger_uuid, force=True) is None
        runs = await db.scalar(select(func.count()).select_from(AgentTriggerRun))
        assert runs == 0  # no durable run is claimed for a revoked owner
    allowed = _service("enforce", {(USER, "can_run", f"agent:{agent_id}")})
    monkeypatch.setattr(runtime, "_current", allowed)
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_uuid)
        assert trigger is not None
        context = await require_trigger_actor(db, trigger)
        assert context.user_id == TEST_USER_ID


@pytest.mark.asyncio
async def test_fire_time_legacy_is_a_no_op(client: AsyncClient, monkeypatch) -> None:
    _agent_id, trigger_id = await _agent_with_trigger(client)
    legacy = _service("legacy")
    monkeypatch.setattr(runtime, "_current", legacy)
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, uuid.UUID(trigger_id))
        assert trigger is not None
        await require_trigger_actor(db, trigger)
    assert legacy._client.queries == []  # type: ignore[attr-defined]
