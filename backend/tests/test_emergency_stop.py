"""Emergency stop (feature plan v2 순번 13, 17.4): one action stops an agent, a person's
agents or an MCP server; nothing new runs until someone with a reason releases it."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import emergency, service
from app.exceptions import AppError
from app.models.agent_api import AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.approval_request import ApprovalRequest
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.mcp_server import McpServer
from app.models.notification import Notification
from tests.authz.e09_support import OUTSIDER, acting_as, make_admin, seed_outsider
from tests.authz.g03_support import approval_payload, seed_conversation, seed_interrupted
from tests.authz.unit_scope_fixture import TEST_ORG_ID
from tests.conftest import TEST_USER_ID, TestSession

URL = "/api/emergency-stops"


async def _agent_with_work(db: AsyncSession) -> tuple[Conversation, dict[str, Any]]:
    """An agent with a running run, an active schedule, a deployment and a pending request."""
    conversation = await seed_conversation(db)
    agent_id = conversation.agent_id
    assert agent_id is not None
    interrupted = await db.get(
        ConversationRun, await seed_interrupted(db, conversation, approval_payload())
    )
    assert interrupted is not None
    interrupted.tenant_id, interrupted.org_id = conversation.tenant_id, conversation.org_id
    other = await seed_conversation(db)  # a second conversation of a second agent
    running = ConversationRun(
        conversation_id=other.id,
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="running",
        is_active=True,
        tenant_id=conversation.tenant_id,
        org_id=conversation.org_id,
    )
    trigger = AgentTrigger(
        org_id=TEST_ORG_ID,
        tenant_id=conversation.tenant_id,
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        trigger_type="interval",
        schedule_config={"interval_minutes": 30},
        input_message="run",
        status="active",
    )
    deployment = AgentDeployment(
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        public_id=f"dep-{uuid.uuid4().hex[:8]}",
        status="active",
        org_id=TEST_ORG_ID,
        tenant_id=conversation.tenant_id,
    )
    db.add_all([running, trigger, deployment])
    await db.commit()
    [request] = await service.record_pending(
        db,
        interrupted,
        [service.PendingInterrupt("intr-1", approval_payload())],
        origin=service.RunOrigin("Protocol Agent"),
    )
    await db.commit()
    return conversation, {
        "agent_id": agent_id,
        "running": running.id,
        "trigger": trigger.id,
        "deployment": deployment.id,
        "request": request.id,
    }


async def _stop(client: AsyncClient, scope_type: str, scope_id: uuid.UUID) -> Any:
    return await client.post(
        URL, json={"scope_type": scope_type, "scope_id": str(scope_id), "reason": "오발송 의심"}
    )


@pytest.mark.asyncio
async def test_stopping_an_agent_blocks_first_then_stops_everything_it_has_going(
    client: AsyncClient, db: AsyncSession
) -> None:
    _conversation, work = await _agent_with_work(db)

    response = await _stop(client, "agent", work["agent_id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["scope_name"]) == ("active", "Protocol Agent")
    assert body["state"] == {
        "block_received": True,
        "new_calls_blocked": True,
        "runs_cancel_requested": 1,
        "runs_cancel_confirmed": 0,
        "schedules_paused": 1,
        "deployments_disabled": 1,
        "approvals_canceled": 1,
        "external_side_effects": "unknown",
    }
    async with TestSession() as check:
        run = await check.get(ConversationRun, work["running"])
        trigger = await check.get(AgentTrigger, work["trigger"])
        deployment = await check.get(AgentDeployment, work["deployment"])
        request = await check.get(ApprovalRequest, work["request"])
    assert run is not None and (run.status, run.cancel_reason) == ("canceling", "emergency_stop")
    assert trigger is not None and trigger.status == "paused"
    assert deployment is not None and deployment.status == "disabled"
    assert request is not None and (request.status, request.channel) == (
        "canceled",
        "emergency_stop",
    )
    # The same target again is the same stop.
    again = await _stop(client, "agent", work["agent_id"])
    assert again.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_a_stopped_agent_starts_nothing_new_not_even_a_resume(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation, work = await _agent_with_work(db)
    await _stop(client, "agent", work["agent_id"])

    with pytest.raises(AppError) as refused:
        await emergency.refuse_run(db, tenant_id=conversation.tenant_id, agent_id=work["agent_id"])
    assert refused.value.code == emergency.STOPPED_CODE
    resume = await client.post(
        f"/api/conversations/{conversation.id}/messages/resume",
        json={"decisions": [{"type": "approve"}, {"type": "approve"}]},
    )
    assert resume.status_code == 409
    assert resume.json()["error"]["code"] == emergency.STOPPED_CODE


@pytest.mark.asyncio
async def test_a_stop_of_a_person_covers_every_agent_they_own(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation, work = await _agent_with_work(db)
    second = await seed_conversation(db)
    assert second.agent_id is not None

    response = await _stop(client, "user", TEST_USER_ID)

    assert response.status_code == 201, response.text
    for agent_id in (work["agent_id"], second.agent_id):
        assert await emergency.stop_for_agent(
            db, tenant_id=conversation.tenant_id, agent_id=agent_id
        )


@pytest.mark.asyncio
async def test_someone_unrelated_cannot_stop_an_agent_but_a_company_admin_can(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    from tests.authz.d04_support import OTHER_ORG_ID

    _conversation, work = await _agent_with_work(db)
    await seed_outsider(db)
    await db.commit()

    with acting_as(test_app, OUTSIDER, OTHER_ORG_ID):
        refused = await _stop(client, "agent", work["agent_id"])
        assert refused.status_code == 404
        await make_admin(db, OUTSIDER.id)
        allowed = await _stop(client, "agent", work["agent_id"])
    assert allowed.status_code == 201, allowed.text

    # The owner hears about a stop someone else made.
    async with TestSession() as check:
        [note] = list(
            await check.scalars(
                select(Notification).where(Notification.kind == "emergency_stop.applied")
            )
        )
    assert note.user_id == TEST_USER_ID and note.actor_id == OUTSIDER.id


@pytest.mark.asyncio
async def test_a_release_needs_a_reason_restores_the_deployments_and_keeps_schedules_paused(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation, work = await _agent_with_work(db)
    stop_id = (await _stop(client, "agent", work["agent_id"])).json()["id"]

    assert (await client.post(f"{URL}/{stop_id}/release", json={})).status_code == 422
    released = await client.post(f"{URL}/{stop_id}/release", json={"reason": "확인 완료"})

    assert released.status_code == 200, released.text
    assert released.json()["status"] == "released"
    assert released.json()["state"]["new_calls_blocked"] is False
    async with TestSession() as check:
        deployment = await check.get(AgentDeployment, work["deployment"])
        trigger = await check.get(AgentTrigger, work["trigger"])
    assert deployment is not None and deployment.status == "active"
    assert trigger is not None and trigger.status == "paused"  # its owner resumes it
    await emergency.refuse_run(db, tenant_id=conversation.tenant_id, agent_id=work["agent_id"])
    assert (await client.get(URL)).json()["items"] == []
    history = (await client.get(f"{URL}?active=false")).json()["items"]
    assert [item["release_reason"] for item in history] == ["확인 완료"]


@pytest.mark.asyncio
async def test_only_the_owner_or_a_company_admin_releases(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    from app.models.organization import OrganizationRoleGrant

    _conversation, work = await _agent_with_work(db)
    await seed_outsider(db)
    # An organization admin of the agent's organization may stop it, not release it.
    db.add(
        OrganizationRoleGrant(
            org_id=TEST_ORG_ID,
            tenant_id=_conversation.tenant_id,
            principal_type="user",
            principal_id=OUTSIDER.id,
            role="admin",
        )
    )
    await db.commit()
    with acting_as(test_app, OUTSIDER, TEST_ORG_ID):
        stopped = await _stop(client, "agent", work["agent_id"])
        assert stopped.status_code == 201, stopped.text
        refused = await client.post(
            f"{URL}/{stopped.json()['id']}/release", json={"reason": "해제"}
        )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_a_stopped_mcp_server_takes_no_call(client: AsyncClient, db: AsyncSession) -> None:
    from langchain_core.tools import tool

    from app.agent_runtime.mcp_cache import MCPToolWithRetry
    from app.mcp.decisions import DecisionContext

    conversation = await seed_conversation(db)
    server = McpServer(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=conversation.tenant_id,
        name="Mail",
        transport="streamable_http",
        url="https://mail.example.com/mcp",
        is_system=False,
    )
    db.add(server)
    await db.commit()
    calls: list[str] = []

    @tool
    def send(body: str) -> str:
        """Send."""
        calls.append(body)
        return "sent"

    wrapped = MCPToolWithRetry(
        send,
        decision_context=DecisionContext(
            tenant_id=conversation.tenant_id,
            org_id=TEST_ORG_ID,
            user_id=TEST_USER_ID,
            agent_id=conversation.agent_id,
            conversation_id=str(conversation.id),
            server_id=str(server.id),
            tool_id=None,
            tool_name="send",
            reviewed_fingerprint=None,
            risk_level=None,
            gateway=False,
        ),
    )
    assert await wrapped.ainvoke({"body": "a"}) == "sent"

    assert (await _stop(client, "mcp_server", server.id)).status_code == 201
    refused = await wrapped.ainvoke({"body": "b"})

    assert "emergency-stopped" in str(refused) and calls == ["a"]
    async with TestSession() as check:
        decisions = list(
            await check.scalars(
                select(AuditEvent.reason_code).where(AuditEvent.action == "mcp.tool_call")
            )
        )
    assert "emergency_stop" in decisions
