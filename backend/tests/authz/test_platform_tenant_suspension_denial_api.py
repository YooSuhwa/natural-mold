"""Operator suspension freshly denies new company requests; resume restores only status."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.config import settings
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentApiRun, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from tests.authz.platform_tenant_suspension_workload import (
    SuspensionWorkload,
    seed_suspension_workload,
)
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.platform_tenant_lifecycle_app import LifecycleCase

pytest_plugins = ["tests.authz.platform_tenant_lifecycle_app"]


def _code(response: Response) -> str:
    return response.json()["error"]["code"]


async def _count(model: type, *criteria: object) -> int:
    async with TestSession() as db:
        query = select(func.count()).select_from(model)
        for criterion in criteria:
            query = query.where(criterion)  # type: ignore[arg-type]
        return int(await db.scalar(query) or 0)


async def _api_usage(work: SuspensionWorkload) -> int:
    async with TestSession() as db:
        key = await db.get(AgentApiKey, work.api_key_id)
        assert key is not None
        return key.usage_count


async def _suspend(case: LifecycleCase) -> None:
    response = await case.transition("suspend")
    assert response.status_code == 200, response.text


async def _new_private_requests(
    case: LifecycleCase, work: SuspensionWorkload
) -> dict[str, Response]:
    member, csrf = case.member, case.member_headers()
    return {
        "private_read": await member.get("/api/agents"),
        "conversation_read": await member.get(f"/api/conversations/{work.conversation_id}"),
        "private_write": await member.post(
            "/api/agents",
            json={
                "name": "During suspension",
                "system_prompt": "x",
                "model_id": str(work.agent_id),
            },
            headers=csrf,
        ),
        "chat_send": await member.post(
            f"/api/conversations/{work.conversation_id}/messages",
            json={"content": "hello"},
            headers=csrf,
        ),
        "chat_start": await member.post(
            f"/api/agents/{work.agent_id}/conversations/start",
            json={"content": "hello"},
            headers=csrf,
        ),
        "trigger_run_now": await member.post(
            f"/api/triggers/{work.trigger_id}/run-now", headers=csrf
        ),
        "governance_read": await member.get(f"/api/tenants/{case.tenant_id}"),
        "governance_write": await member.patch(
            f"/api/tenants/{case.tenant_id}",
            json={"name": "Renamed during suspension"},
            headers={**csrf, "If-Match": await case.version()},
        ),
    }


async def _assert_api_lists(case: LifecycleCase, work: SuspensionWorkload) -> None:
    listed = await case.member.get("/v1/agents", headers=_api(work))
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["data"]] == [work.deployment_public_id]


def _api(work: SuspensionWorkload) -> dict[str, str]:
    return {"Authorization": f"Bearer {work.api_key}"}


@pytest.mark.parametrize("prepared_leases", [False, True])
async def test_suspension_denies_every_representative_new_request(
    db: AsyncSession,
    lifecycle_case: LifecycleCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    prepared_leases: bool,
) -> None:
    case = lifecycle_case
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", prepared_leases)
    monkeypatch.setattr(trigger_executor, "async_session", TestSession)
    work = await seed_suspension_workload(db, case)
    assert (await case.member.get("/api/agents")).status_code == 200
    assert (await case.member.get(f"/api/tenants/{case.tenant_id}")).status_code == 200
    api_headers = _api(work)
    await _assert_api_lists(case, work)
    usage = await _api_usage(work)
    agents, runs = await _count(Agent), await _count(ConversationRun)

    await _suspend(case)
    denied = await _new_private_requests(case, work)

    assert {name: r.status_code for name, r in denied.items()} == dict.fromkeys(denied, 403)
    assert {name: _code(r) for name, r in denied.items()} == dict.fromkeys(
        denied, "tenant_suspended"
    )
    # The persisted-org Agent API key keeps its existing redacted 401 contract.
    api_list = await case.member.get("/v1/agents", headers=api_headers)
    api_thread = await case.member.post(
        "/v1/threads", headers=api_headers, json={"agent_id": work.deployment_public_id}
    )
    assert [api_list.status_code, api_thread.status_code] == [401, 401]
    assert {_code(api_list), _code(api_thread)} == {"AGENT_API_KEY_INVALID"}
    assert await _api_usage(work) == usage
    # The scheduler entry is denied by the fresh prefix before any run row.
    with caplog.at_level(logging.INFO, logger="app.agent_runtime.trigger_executor"):
        assert await trigger_executor.execute_trigger(str(work.trigger_id)) is None
    assert "authorization: tenant_suspended" in caplog.text
    assert await _count(AgentTriggerRun) == 0 and await _count(AgentApiRun) == 0
    assert (await _count(Agent), await _count(ConversationRun)) == (agents, runs)


async def test_suspension_keeps_navigation_metadata_and_refresh_tokens(
    db: AsyncSession, lifecycle_case: LifecycleCase
) -> None:
    case = lifecycle_case
    await seed_suspension_workload(db, case)
    await _suspend(case)

    navigation = await case.member.get("/api/me/organizations")
    assert navigation.status_code == 200, navigation.text
    summaries = [row for row in navigation.json() if row["tenant_id"] == str(case.tenant_id)]
    assert [row["tenant_status"] for row in summaries] == ["suspended"]
    assert set(summaries[0]) >= {"id", "name", "tenant_name", "tenant_status"}
    installation = await case.member.get("/api/me/installation")
    assert installation.status_code == 200

    async with TestSession() as check:
        tokens = (
            await check.scalars(
                select(RefreshToken.revoked_at).where(RefreshToken.user_id == case.member_id)
            )
        ).all()
    assert tokens and all(revoked is None for revoked in tokens)


async def test_resume_restores_access_without_reactivating_entry_points(
    db: AsyncSession, lifecycle_case: LifecycleCase
) -> None:
    case = lifecycle_case
    work = await seed_suspension_workload(db, case)
    await _suspend(case)
    resumed = await case.transition("resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"

    assert (await case.member.get("/api/agents")).status_code == 200
    assert (await case.member.get(f"/api/tenants/{case.tenant_id}")).status_code == 200
    await _assert_api_lists(case, work)
    async with TestSession() as check:
        archived = await check.get(Organization, work.archived_org_id)
        paused = await check.get(AgentTrigger, work.paused_trigger_id)
        active = await check.get(AgentTrigger, work.trigger_id)
        disabled = await check.get(AgentDeployment, work.disabled_deployment_id)
        running = await check.get(ConversationRun, work.running_run_id)
    assert archived is not None and archived.status == "archived"
    assert archived.archived_at is not None
    assert paused is not None and paused.status == "paused"
    assert active is not None and active.status == "active"
    assert disabled is not None and disabled.status == "disabled"
    assert running is not None and running.status == "running"
