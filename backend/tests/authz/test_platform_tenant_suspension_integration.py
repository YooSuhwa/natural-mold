"""Native PostgreSQL operator suspension: races, stale/non-operator and fresh denial.

Runs only in the canonical runner lane (``suspension_lane``). The in-flight
permit node encodes the Task-10 permit contract "lock -> decide -> release ->
leaf I/O": a suspend must commit while a granted leaf is still running, and
only the next permit observes ``tenant_suspended``.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.agent_runtime import trigger_executor
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_run_registry import reset_prepared_run_registry_for_tests
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.services import audit_service, conversation_run_worker
from tests.authz.platform_tenant_suspension_native_fixture import (
    NativeSuspension,
    SuspensionLane,
    native_suspension,
)
from tests.authz.platform_tenant_suspension_workload import activate_lease, provider_permit
from tests.authz.task10_runtime_barriers import assert_blocked, finish, park_after

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.platform_tenant_suspension_native_fixture"]
STALE = '"2001-01-01T00:00:00+00:00"'


async def _audits(case: NativeSuspension, action: str) -> int:
    async with case.lane.sessions() as db:
        query = select(func.count()).select_from(AuditEvent)
        query = query.where(
            AuditEvent.action == action, AuditEvent.target_id == str(case.context.tenant_id)
        )
        return int(await db.scalar(query) or 0)


async def _owned_rows(case: NativeSuspension, model: type) -> int:
    async with case.lane.sessions() as db:
        query = select(func.count()).select_from(model)
        query = query.where(model.user_id == case.context.user_id)  # type: ignore[attr-defined]
        return int(await db.scalar(query) or 0)


async def test_native_concurrent_suspends_have_one_winner_and_one_audit(
    suspension_lane: SuspensionLane,
) -> None:
    async with native_suspension(suspension_lane) as case:
        version = await case.version()
        gate = park_after(suspension_lane.monkeypatch, audit_service, "record_event")
        first = asyncio.create_task(case.transition("suspend", version=version))
        await gate.wait_entered()
        second = asyncio.create_task(case.transition("suspend", client=case.rival, version=version))
        await assert_blocked(second)
        gate.release.set()
        winner, loser = await finish(first), await finish(second)

        assert winner.status_code == 200 and winner.json()["status"] == "suspended"
        # The loser re-reads the locked row: its version is now stale.
        assert loser.status_code == 409 and loser.json()["error"]["code"] == "stale_version"
        assert await case.status() == "suspended"
        assert await _audits(case, "tenant.suspend") == 1
        repeated = await case.transition("suspend")
        assert repeated.status_code == 200 and repeated.headers["etag"] == winner.headers["etag"]
        assert await _audits(case, "tenant.suspend") == 1


async def test_native_stale_resume_and_nonoperator_leave_state_and_audit(
    suspension_lane: SuspensionLane,
) -> None:
    async with native_suspension(suspension_lane) as case:
        denied = await case.transition("suspend", client=case.member)
        assert denied.status_code == 403
        assert await case.status() == "active" and await _audits(case, "tenant.suspend") == 0

        assert (await case.transition("suspend")).status_code == 200
        stale = await case.transition("resume", version=STALE)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
        assert await case.status() == "suspended"
        assert await _audits(case, "tenant.resume") == 0
        assert (await case.transition("resume")).status_code == 200
        assert await case.status() == "active"


async def test_native_suspension_denies_new_private_work_with_zero_starts(
    suspension_lane: SuspensionLane,
) -> None:
    async with native_suspension(suspension_lane) as case:
        member, ctx = case.member, case.context
        conversation = case.graph.conversation_ids[0]
        api = {"Authorization": f"Bearer {case.api_key}"}
        before = [await _owned_rows(case, model) for model in (Agent, ConversationRun)]
        assert (await case.transition("suspend")).status_code == 200

        responses = {
            "read": await member.get("/api/agents"),
            "write": await member.post(
                "/api/agents",
                json={"name": "x", "system_prompt": "x", "model_id": str(uuid4())},
            ),
            "chat": await member.post(
                f"/api/conversations/{conversation}/messages", json={"content": "hi"}
            ),
            "trigger": await member.post(f"/api/triggers/{case.trigger_id}/run-now"),
            "governance": await member.get(f"/api/tenants/{ctx.tenant_id}"),
        }
        assert {k: (r.status_code, r.json()["error"]["code"]) for k, r in responses.items()} == (
            dict.fromkeys(responses, (403, "tenant_suspended"))
        )
        api_run = await member.post(
            "/v1/threads", headers=api, json={"agent_id": case.deployment_public_id}
        )
        assert api_run.status_code == 401
        assert api_run.json()["error"]["code"] == "AGENT_API_KEY_INVALID"
        assert await trigger_executor.execute_trigger(str(case.trigger_id)) is None

        after = [await _owned_rows(case, model) for model in (Agent, ConversationRun)]
        assert after == before
        assert await _owned_rows(case, AgentTriggerRun) == 0
        assert await _owned_rows(case, AgentApiRun) == 0
        assert conversation_run_worker.get_run_task_registry().active_run_ids() == set()


async def test_native_suspend_does_not_wait_for_in_flight_leaf_and_next_permit_denies(
    suspension_lane: SuspensionLane,
) -> None:
    async with native_suspension(suspension_lane) as case:
        graph = case.graph
        lease = await activate_lease(
            graph.context, graph.agent_id, graph.conversation_ids[0], uuid4()
        )
        try:
            async with provider_permit(lease, suspension_lane.sessions):
                # The leaf is "in flight": the permit must already have released locks.
                suspended = await asyncio.wait_for(case.transition("suspend"), 5)
                assert suspended.status_code == 200
            with pytest.raises(DispatchDeniedError) as denied:
                async with provider_permit(lease, suspension_lane.sessions):
                    pytest.fail("a suspended company must not receive a dispatch permit")
            assert denied.value.code == "tenant_suspended"
        finally:
            reset_prepared_run_registry_for_tests()
