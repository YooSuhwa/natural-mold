"""Schedule rules (feature plan v2 순번 13, 17.3): shortest repeat, count per person,
automatic pause after repeated failures, and the per-schedule approval policy."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.agent_trigger import AgentTrigger
from app.models.organization import Tenant
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession
from tests.test_triggers import _create_agent, _create_model


async def _agent(client: AsyncClient) -> str:
    return await _create_agent(client, await _create_model(client))


async def _create(client: AsyncClient, agent_id: str, **body: object):
    return await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 15},
            "input_message": "run",
            **body,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger_type", "config"),
    [
        ("interval", {"interval_minutes": 14}),
        ("cron", {"cron_expression": "*/10 * * * *"}),
        ("cron", {"cron_expression": "0,5 9 * * *"}),  # two firings five minutes apart
    ],
)
async def test_a_schedule_repeats_at_most_every_fifteen_minutes(
    client: AsyncClient, trigger_type: str, config: dict[str, object]
) -> None:
    agent_id = await _agent(client)

    refused = await _create(client, agent_id, trigger_type=trigger_type, schedule_config=config)

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "SCHEDULE_TOO_FREQUENT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger_type", "config"),
    [
        ("interval", {"interval_minutes": 15}),
        ("cron", {"cron_expression": "*/15 * * * *"}),
        ("cron", {"cron_expression": "0 9 * * 1-5"}),
    ],
)
async def test_fifteen_minutes_or_slower_is_accepted(
    client: AsyncClient, trigger_type: str, config: dict[str, object]
) -> None:
    agent_id = await _agent(client)

    created = await _create(client, agent_id, trigger_type=trigger_type, schedule_config=config)

    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_an_update_cannot_make_a_schedule_too_frequent(client: AsyncClient) -> None:
    agent_id = await _agent(client)
    trigger_id = (await _create(client, agent_id)).json()["id"]

    refused = await client.patch(
        f"/api/triggers/{trigger_id}", json={"schedule_config": {"interval_minutes": 5}}
    )

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "SCHEDULE_TOO_FREQUENT"


@pytest.mark.asyncio
async def test_one_person_keeps_at_most_the_company_limit_of_schedules(
    client: AsyncClient,
) -> None:
    agent_id = await _agent(client)
    async with TestSession() as db:
        tenant = await db.get(Tenant, TEST_TENANT_ID)
        assert tenant is not None
        tenant.settings = {**(tenant.settings or {}), "schedules": {"max_per_user": 2}}
        await db.commit()
    first, second = (await _create(client, agent_id)), (await _create(client, agent_id))
    assert (first.status_code, second.status_code) == (201, 201)

    refused = await _create(client, agent_id)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "SCHEDULE_LIMIT_REACHED"

    # A finished schedule frees its place.
    async with TestSession() as db:
        await db.execute(
            update(AgentTrigger)
            .where(AgentTrigger.id == uuid.UUID(first.json()["id"]))
            .values(status="completed")
        )
        await db.commit()
    assert (await _create(client, agent_id)).status_code == 201


@pytest.mark.asyncio
async def test_the_installation_limit_is_twenty(client: AsyncClient) -> None:
    from app.config import settings

    assert settings.trigger_max_per_user == 20
    assert settings.trigger_min_interval_minutes == 15


@pytest.mark.asyncio
async def test_new_schedules_pause_after_ten_failures_and_wait_for_the_inbox(
    client: AsyncClient,
) -> None:
    agent_id = await _agent(client)

    created = (await _create(client, agent_id)).json()
    explicit_off = (await _create(client, agent_id, auto_pause_after_failures=None)).json()
    blocked = (await _create(client, agent_id, approval_policy="blocked")).json()

    assert (created["auto_pause_after_failures"], created["approval_policy"]) == (10, "inbox")
    assert explicit_off["auto_pause_after_failures"] is None
    assert blocked["approval_policy"] == "blocked"
    switched = await client.patch(
        f"/api/triggers/{blocked['id']}", json={"approval_policy": "inbox"}
    )
    assert switched.json()["approval_policy"] == "inbox"
    bad = await _create(client, agent_id, approval_policy="sometimes")
    assert bad.status_code == 422
