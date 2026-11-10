"""Registration review, per-person tool blocks and the company catalog switch (T5).

tool-governance-plan 3.6, 3.7, 3.11: under review a member's tool or MCP server waits for
an administrator; nobody attaches it and no run loads it meanwhile. A server manager keeps
named people or teams from one tool, and a call already under way is refused.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from langchain_core.tools import tool as lc_tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.mcp_cache import MCPToolWithRetry
from app.approvals import inbox, service
from app.authz import tool_blocks
from app.authz.registration_state import fingerprint, registered
from app.dependencies import CurrentUser
from app.guardrails import policy
from app.mcp import discovery, review
from app.mcp.decisions import DecisionContext
from app.models.approval_request import ApprovalRequest
from app.models.audit_event import AuditEvent
from app.models.authz import ResourceGrant
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.notification import Notification
from app.models.organization import OrganizationRoleGrant, Tenant
from app.models.tool import AgentToolLink
from app.services import audit_service, chat_service
from tests.authz.d04_support import OTHER_ORG_ID, OTHER_USER_ID, add_server, add_tool, seed_scope
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_group, add_member, give_cap
from tests.authz.e01_support import make_admin as make_org_admin
from tests.authz.e09_support import (
    MEMBER,
    OUTSIDER,
    OUTSIDER_ID,
    acting_as,
    actions,
    make_admin,
    seed_outsider,
)
from tests.authz.f05_support import identity, loaded
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

REVIEW = {"who": "members", "review": "on", "member_sharing": "shareable"}
TOOL = {"definition_key": "http_request", "name": "n", "parameters": {}}
SERVER = {"name": "s", "transport": "streamable_http", "url": "https://mcp.example.com"}
APPROVE = {"decisions": [{"type": "approve"}]}
ADMIN = CurrentUser(id=OTHER_USER_ID, email="other@test.com", name="Other", is_super_user=False)
THIRD = CurrentUser(id=THIRD_USER_ID, email="third@test.com", name="Third", is_super_user=False)


@pytest.fixture
async def _member(db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch):
    """A plain member acts; OTHER is the company administrator; review is on."""
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    await make_admin(db, OTHER_USER_ID)
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    settings: dict[str, Any] = dict(tenant.settings or {})
    settings["registration"] = {"tool": REVIEW, "mcp_server": REVIEW}
    tenant.settings = settings
    await db.commit()
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _requests(resource_id: UUID | str, status: str | None = None) -> list[ApprovalRequest]:
    async with TestSession() as db:
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.kind == "registration",
            ApprovalRequest.resource_id == UUID(str(resource_id)),
        )
        if status is not None:
            stmt = stmt.where(ApprovalRequest.status == status)
        return list((await db.scalars(stmt.order_by(ApprovalRequest.created_at))).all())


async def _pending(resource_id: UUID | str) -> ApprovalRequest:
    (row,) = await _requests(resource_id, "pending")
    return row


async def _notes(user_id: UUID, kind: str) -> list[Notification]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(Notification).where(Notification.user_id == user_id, Notification.kind == kind)
        )
        return list(rows)


async def _server(server_id: UUID | str) -> McpServer:
    async with TestSession() as db:
        row = await db.get(McpServer, UUID(str(server_id)))
        assert row is not None
        return row


async def _events(target_id: UUID | str, action: str) -> list[AuditEvent]:
    return [event for event in await actions(target_id) if event.action == action]


async def _new_server(client: AsyncClient) -> str:
    created = await client.post("/api/mcp-servers", json=SERVER)
    assert created.status_code == 201, created.text
    assert created.json()["registration_state"] == "pending"
    return created.json()["id"]


async def _decide(
    client: AsyncClient, test_app: FastAPI, row_id: UUID, body: dict[str, Any], who=ADMIN, org=None
):
    with acting_as(test_app, who, org or TEST_ORG_ID):
        return await client.post(f"/api/approvals/{row_id}/decision", json=body)


async def _approved_server(db: AsyncSession) -> tuple[McpServer, McpTool]:
    """A member's server an administrator approved (its fingerprint is on record)."""
    server, tool = await add_server(db)
    tool.review_state = "approved"
    server.registration_fingerprint = fingerprint(server)
    await db.commit()
    return server, tool


# --- a registration waits ----------------------------------------------------------------


@pytest.mark.usefixtures("_member")
async def test_a_members_server_waits_and_the_administrators_are_asked(
    client: AsyncClient, db: AsyncSession
) -> None:
    server_id = await _new_server(client)

    row = await _pending(server_id)
    assert (row.kind, row.source, row.resource_type) == (
        "registration",
        "registration",
        "mcp_server",
    )
    assert (row.subject_user_id, row.org_id) == (TEST_USER_ID, TEST_ORG_ID)
    args = row.actions[0]["args"]
    assert (args["reason"], args["url"], args["transport"]) == (
        "new",
        "https://mcp.example.com",
        "streamable_http",
    )
    (event,) = await _events(server_id, "registration.requested")
    assert (event.actor_user_id, event.outcome) == (TEST_USER_ID, "success")
    assert event.event_metadata == {
        "approval_request_id": str(row.id),
        "review_reason": "new",
        "registration_state": "pending",
    }
    (note,) = await _notes(OTHER_USER_ID, "approval.requested")
    assert (note.target_id, note.actor_id) == (row.id, TEST_USER_ID)
    assert await _notes(TEST_USER_ID, "approval.requested") == []  # the registrant is not asked


@pytest.mark.usefixtures("_member")
async def test_a_members_tool_waits_too(client: AsyncClient) -> None:
    created = await client.post("/api/tools", json=TOOL)
    assert created.status_code == 201, created.text
    assert created.json()["registration_state"] == "pending"
    row = await _pending(created.json()["id"])
    assert row.resource_type == "tool"
    assert row.actions[0]["args"]["definition_key"] == "http_request"


@pytest.mark.usefixtures("_member")
async def test_a_capability_holder_registers_without_waiting(
    client: AsyncClient, db: AsyncSession
) -> None:
    await give_cap(db, "cap_register_mcp")
    created = await client.post("/api/mcp-servers", json=SERVER)
    assert created.status_code == 201, created.text
    assert created.json()["registration_state"] == "approved"
    assert await _requests(created.json()["id"]) == []
    # The tool rule is its own: without that capability the same person's tool waits.
    waiting = await client.post("/api/tools", json=TOOL)
    assert waiting.json()["registration_state"] == "pending"


async def test_with_review_off_nothing_waits(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        created = await client.post("/api/mcp-servers", json=SERVER)
    assert created.json()["registration_state"] == "approved"
    assert await _requests(created.json()["id"]) == []


# --- nobody uses it meanwhile --------------------------------------------------------------


@pytest.mark.usefixtures("_member")
async def test_an_unapproved_tool_or_server_is_not_attached_to_an_agent(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    agent = await add_agent(db)
    tool_id = (await client.post("/api/tools", json=TOOL)).json()["id"]
    server, mcp_tool = await add_server(db)
    server.registration_state = "pending"
    await db.commit()

    for body, kind, resource_id in (
        ({"tool_ids": [tool_id]}, "tool", tool_id),
        ({"mcp_tool_ids": [str(mcp_tool.id)]}, "mcp_server", str(server.id)),
    ):
        refused = await client.put(f"/api/agents/{agent.id}", json=body)
        assert refused.status_code == 409, refused.text
        error = refused.json()["error"]
        assert error["code"] == "registration_not_approved"
        assert error["details"] == {
            "resources": [{"resource_type": kind, "resource_id": resource_id}]
        }
    async with TestSession() as fresh:
        assert (await fresh.scalars(select(AgentToolLink))).all() == []
        assert (await fresh.scalars(select(AgentMcpToolLink))).all() == []

    row = await _pending(tool_id)
    assert (await _decide(client, test_app, row.id, APPROVE)).status_code == 200
    attached = await client.put(f"/api/agents/{agent.id}", json={"tool_ids": [tool_id]})
    assert attached.status_code == 200, attached.text


@pytest.mark.usefixtures("_member")
@pytest.mark.parametrize("state", ["pending", "rejected"])
async def test_a_run_does_not_load_what_is_not_registered(db: AsyncSession, state: str) -> None:
    agent = await add_agent(db)
    tool = await add_tool(db)
    server, mcp_tool = await add_server(db)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()

    async def names() -> set[str]:
        configs = await chat_service.build_tools_config(
            await loaded(db, agent),
            context=unit_context(TEST_USER_ID),
            db=db,
            identity=identity(agent, TEST_USER_ID),
        )
        return {config["name"] for config in configs}

    assert await names() == {tool.name, mcp_tool.name}
    tool.registration_state = server.registration_state = state
    await db.commit()
    assert await names() == set()
    tool.registration_state = server.registration_state = "approved"
    await db.commit()
    assert await names() == {tool.name, mcp_tool.name}


async def test_a_server_changed_after_approval_is_not_registered(db: AsyncSession) -> None:
    await seed_scope(db)
    server, _ = await _approved_server(db)
    assert registered(server)
    server.url = "https://elsewhere.example.test"
    assert not registered(server)
    server.url, server.headers = "https://mcp.example.test", {"X-Team": "a"}
    assert not registered(server)  # a new header name is a new way to authenticate
    server.headers = {}
    assert registered(server)
    server.name = "renamed"
    assert registered(server)


# --- who decides -------------------------------------------------------------------------


@pytest.mark.usefixtures("_member")
async def test_only_administrators_see_and_decide_the_request(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    await add_member(db, THIRD_USER_ID)
    await seed_outsider(db)
    db.add(
        OrganizationRoleGrant(
            org_id=OTHER_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            role="admin",
            principal_type="user",
            principal_id=OUTSIDER_ID,
        )
    )
    await db.commit()
    server_id = await _new_server(client)
    row = await _pending(server_id)

    async def sees(who: CurrentUser, org: UUID) -> tuple[list[str], int]:
        with acting_as(test_app, who, org):
            page = await client.get("/api/approvals", params={"status": "pending"})
            count = await client.get("/api/approvals/pending-count")
        return [item["id"] for item in page.json()["items"]], count.json()["count"]

    # The registrant, a plain member and the sibling organization's administrator: nothing.
    for who, org in ((MEMBER, TEST_ORG_ID), (THIRD, TEST_ORG_ID), (OUTSIDER, OTHER_ORG_ID)):
        assert await sees(who, org) == ([], 0)
        refused = await _decide(client, test_app, row.id, APPROVE, who=who, org=org)
        assert refused.status_code == 404
        assert refused.json()["error"]["code"] == "approval_not_found"
    assert (await _server(server_id)).registration_state == "pending"

    assert await sees(ADMIN, TEST_ORG_ID) == ([str(row.id)], 1)
    await make_org_admin(db, THIRD_USER_ID)
    assert await sees(THIRD, TEST_ORG_ID) == ([str(row.id)], 1)
    decided = await _decide(client, test_app, row.id, APPROVE, who=THIRD)
    assert decided.status_code == 200, decided.text
    assert (await _server(server_id)).registration_state == "approved"
    assert await sees(ADMIN, TEST_ORG_ID) == ([], 0)


@pytest.mark.usefixtures("_member")
async def test_approval_registers_the_server_and_tells_the_registrant(
    client: AsyncClient, test_app: FastAPI
) -> None:
    server_id = await _new_server(client)
    row = await _pending(server_id)

    decided = await _decide(client, test_app, row.id, APPROVE)

    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert (body["status"], body["kind"], body["resource_id"]) == (
        "approved",
        "registration",
        server_id,
    )
    server = await _server(server_id)
    assert server.registration_state == "approved"
    assert server.registration_fingerprint == fingerprint(server)
    (event,) = await _events(server_id, "registration.approved")
    assert (event.actor_user_id, event.outcome) == (OTHER_USER_ID, "success")
    assert event.event_metadata is not None
    assert event.event_metadata["approval_request_id"] == str(row.id)
    assert event.event_metadata["registration_state"] == "approved"
    (note,) = await _notes(TEST_USER_ID, "approval.decided")
    assert (note.target_id, note.actor_id) == (row.id, OTHER_USER_ID)
    assert note.payload["status"] == "approved"
    status = (await client.get(f"/api/mcp-servers/{server_id}/registration")).json()
    assert (status["state"], status["can_request"]) == ("approved", False)


@pytest.mark.usefixtures("_member")
async def test_a_rejection_keeps_the_note_and_the_registrant_asks_again(
    client: AsyncClient, test_app: FastAPI
) -> None:
    server_id = await _new_server(client)
    first = await _pending(server_id)
    reject = {"decisions": [{"type": "reject", "message": "  외부 주소는 안 됩니다  "}]}

    assert (await _decide(client, test_app, first.id, reject)).status_code == 200

    assert (await _server(server_id)).registration_state == "rejected"
    (event,) = await _events(server_id, "registration.rejected")
    assert (event.actor_user_id, event.outcome, event.reason_message) == (
        OTHER_USER_ID,
        "denied",
        "외부 주소는 안 됩니다",
    )
    status = (await client.get(f"/api/mcp-servers/{server_id}/registration")).json()
    assert (status["state"], status["can_request"]) == ("rejected", True)
    assert (status["request"]["status"], status["request"]["reason"]) == (
        "rejected",
        "외부 주소는 안 됩니다",
    )

    again = await client.post(f"/api/mcp-servers/{server_id}/registration")
    assert again.status_code == 200, again.text
    assert (again.json()["state"], again.json()["can_request"]) == ("pending", False)
    second = await _pending(server_id)
    assert second.id != first.id
    # Asking twice keeps the one request.
    await client.post(f"/api/mcp-servers/{server_id}/registration")
    assert [row.id for row in await _requests(server_id, "pending")] == [second.id]
    assert len(await _events(server_id, "registration.requested")) == 2


@pytest.mark.usefixtures("_member")
async def test_an_administrator_asking_again_is_the_decision(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    tool_id = (await client.post("/api/tools", json=TOOL)).json()["id"]
    row = await _pending(tool_id)
    await make_admin(db, TEST_USER_ID)  # the registrant becomes a company administrator

    again = await client.post(f"/api/tools/{tool_id}/registration")

    assert again.status_code == 200, again.text
    assert again.json()["state"] == "approved"
    (retired,) = await _requests(tool_id)
    assert (retired.id, retired.status, retired.reason) == (row.id, "canceled", "no_longer_needed")
    (event,) = await _events(tool_id, "registration.approved")
    assert event.actor_user_id == TEST_USER_ID


@pytest.mark.usefixtures("_member")
async def test_one_answer_per_request(client: AsyncClient, test_app: FastAPI) -> None:
    server_id = await _new_server(client)
    row = await _pending(server_id)
    two = {"decisions": [{"type": "approve"}, {"type": "approve"}]}

    invalid = await _decide(client, test_app, row.id, two)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_decision"
    assert (await _server(server_id)).registration_state == "pending"

    assert (await _decide(client, test_app, row.id, APPROVE)).status_code == 200
    late = await _decide(client, test_app, row.id, {"decisions": [{"type": "reject"}]})
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "approval_stale"
    assert (await _server(server_id)).registration_state == "approved"
    assert await _events(server_id, "registration.rejected") == []


@pytest.mark.usefixtures("_member")
async def test_a_card_that_went_stale_is_refreshed_not_approved(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    server_id = await _new_server(client)
    row = await _pending(server_id)
    server = await db.get(McpServer, UUID(server_id))
    assert server is not None
    server.url = "https://swapped.example.com"  # changed behind the card's back
    await db.commit()

    stale = await _decide(client, test_app, row.id, APPROVE)

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "approval_stale"
    assert (await _server(server_id)).registration_state == "pending"
    shown = await _pending(server_id)
    assert shown.id == row.id
    assert shown.actions[0]["args"]["url"] == "https://swapped.example.com"
    assert (await _decide(client, test_app, row.id, APPROVE)).status_code == 200
    assert (await _server(server_id)).registration_state == "approved"


# --- a changed connection waits again ------------------------------------------------------


@pytest.mark.usefixtures("_member")
async def test_changing_the_connection_of_an_approved_server_asks_again(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, _ = await _approved_server(db)
    before = fingerprint(server)

    renamed = await client.patch(f"/api/mcp-servers/{server.id}", json={"name": "renamed"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["registration_state"] == "approved"
    assert await _requests(server.id) == []

    moved = await client.patch(
        f"/api/mcp-servers/{server.id}", json={"url": "https://moved.example.com"}
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["registration_state"] == "pending"
    row = await _pending(server.id)
    assert row.actions[0]["args"]["reason"] == "changed"
    (event,) = await _events(server.id, "registration.reset")
    assert event.actor_user_id == TEST_USER_ID
    assert event.event_metadata is not None
    after = fingerprint(await _server(server.id))
    assert after != before
    assert (
        event.event_metadata["fingerprint_before"],
        event.event_metadata["fingerprint_after"],
    ) == (before, after)


@pytest.mark.usefixtures("_member")
async def test_a_capability_holders_change_stays_registered(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, _ = await _approved_server(db)
    await give_cap(db, "cap_register_mcp")

    moved = await client.patch(
        f"/api/mcp-servers/{server.id}", json={"url": "https://moved.example.com"}
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["registration_state"] == "approved"
    stored = await _server(server.id)
    assert registered(stored) and stored.url == "https://moved.example.com"
    assert await _requests(server.id) == []
    assert await _events(server.id, "registration.reset") == []


# --- tool definitions go to the administrators -----------------------------------------------


def _changed(tool: McpTool, server: McpServer) -> str:
    """A new version of an approved tool waits; its fingerprint."""
    tool.review_state = "approved"
    tool.approved_fingerprint = review.fingerprint(
        tool.name, review.Definition.from_row(tool), server.url
    )
    descriptor = {"name": tool.name, "description": "now it also deletes", "input_schema": {}}
    (flagged,) = review.observe([tool], [descriptor], server.url).flagged
    assert flagged.pending_fingerprint is not None
    return flagged.pending_fingerprint


@pytest.mark.usefixtures("_member")
async def test_the_owner_of_a_reviewed_server_does_not_review_its_tools(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, tool = await _approved_server(db)
    pending = _changed(tool, server)
    await db.commit()

    refused = await client.post(
        f"/api/mcp-servers/{server.id}/tools/{tool.id}/review",
        json={"decision": "approve", "fingerprint": pending},
    )

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "registration_review_required"
    await db.refresh(tool)
    assert tool.pending_fingerprint == pending and tool.description != "now it also deletes"
    status = (await client.get(f"/api/mcp-servers/{server.id}/registration")).json()
    assert status["administrators_review_tools"] is True


async def test_without_review_the_owner_reviews_the_tools(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    await seed_scope(db)
    server, tool = await add_server(db)
    pending = _changed(tool, server)
    await db.commit()
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        approved = await client.post(
            f"/api/mcp-servers/{server.id}/tools/{tool.id}/review",
            json={"decision": "approve", "fingerprint": pending},
        )
        status = (await client.get(f"/api/mcp-servers/{server.id}/registration")).json()
    assert approved.status_code == 200, approved.text
    assert status["administrators_review_tools"] is False


@pytest.mark.usefixtures("_member")
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_administrators_decide_a_changed_tool_and_the_server_stays_registered(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI, decision: str
) -> None:
    server, tool = await _approved_server(db)
    pending = _changed(tool, server)
    await db.commit()

    await discovery.notify_review_needed(db, server, [tool])
    await db.commit()

    row = await _pending(server.id)
    args = row.actions[0]["args"]
    assert args["reason"] == "tools"
    assert [(t["id"], t["fingerprint"], t["change"], t["description"]) for t in args["tools"]] == [
        (str(tool.id), pending, "changed", "now it also deletes")
    ]
    assert len(await _notes(OTHER_USER_ID, "approval.requested")) == 1
    assert await _notes(TEST_USER_ID, discovery.REVIEW_NEEDED_KIND) == []  # not the manager's

    answered = await _decide(client, test_app, row.id, {"decisions": [{"type": decision}]})

    assert answered.status_code == 200, answered.text
    stored = await _server(server.id)
    assert stored.registration_state == "approved" and registered(stored)
    await db.refresh(tool)
    assert tool.pending_fingerprint is None
    if decision == "approve":
        assert (tool.description, tool.approved_fingerprint) == ("now it also deletes", pending)
        (event,) = await _events(server.id, "registration.approved")
    else:
        assert tool.description != "now it also deletes" and tool.rejected_fingerprint == pending
        (event,) = await _events(server.id, "registration.rejected")
    assert tool.reviewed_by == OTHER_USER_ID
    assert event.event_metadata is not None
    assert (event.event_metadata["review_reason"], event.event_metadata["tools"]) == ("tools", 1)


async def test_without_review_a_changed_tool_goes_to_the_servers_managers(
    db: AsyncSession,
) -> None:
    await seed_scope(db)
    server, tool = await add_server(db)
    _changed(tool, server)
    await db.commit()

    await discovery.notify_review_needed(db, server, [tool])
    await db.commit()

    assert await _requests(server.id) == []
    assert len(await _notes(TEST_USER_ID, discovery.REVIEW_NEEDED_KIND)) == 1


# --- nobody answered -----------------------------------------------------------------------


@pytest.mark.usefixtures("_member")
async def test_an_unanswered_request_expires_and_the_registrant_hears(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inbox, "async_session", TestSession)
    server_id = await _new_server(client)
    row = await db.get(ApprovalRequest, (await _pending(server_id)).id)
    assert row is not None
    assert service.aware(row.expires_at) > service.now() + timedelta(days=29)
    row.expires_at = service.now() - timedelta(seconds=1)
    await db.commit()

    assert await inbox.expire_due() == 1

    await db.refresh(row)
    assert (row.status, row.channel) == ("expired", "expiry")
    assert (await _server(server_id)).registration_state == "pending"
    (note,) = await _notes(TEST_USER_ID, "approval.decided")
    assert (note.payload["status"], note.actor_id) == ("expired", None)
    status = (await client.get(f"/api/mcp-servers/{server_id}/registration")).json()
    assert (status["state"], status["can_request"]) == ("pending", True)


# --- blocks: one tool, named people or teams -------------------------------------------------


def _blocks(server: McpServer, tool: McpTool) -> str:
    return f"/api/mcp-servers/{server.id}/tools/{tool.id}/blocks"


async def _grants(tool_id: UUID) -> list[ResourceGrant]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(ResourceGrant).where(
                ResourceGrant.resource_type == "mcp_tool", ResourceGrant.resource_id == tool_id
            )
        )
        return list(rows)


@pytest.fixture
async def _manager(db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


@pytest.mark.usefixtures("_manager")
async def test_a_manager_blocks_and_unblocks_a_person_and_a_team(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, tool = await add_server(db)
    group = await add_group(db, members=(OTHER_USER_ID,))
    url = _blocks(server, tool)

    person = await client.post(
        url, json={"principal_type": "user", "principal_id": str(OTHER_USER_ID)}
    )
    team = await client.post(url, json={"principal_type": "group", "principal_id": str(group)})

    assert (person.status_code, team.status_code) == (201, 201), (person.text, team.text)
    assert (team.json()["principal_type"], team.json()["member_count"]) == ("group", 1)
    assert person.json()["granted_by"] == str(TEST_USER_ID)
    listed = (await client.get(url)).json()["items"]
    assert [(b["principal_type"], b["principal_id"]) for b in listed] == [
        ("user", str(OTHER_USER_ID)),
        ("group", str(group)),
    ]
    # Blocking the same person again answers the block that stands.
    again = await client.post(
        url, json={"principal_type": "user", "principal_id": str(OTHER_USER_ID)}
    )
    assert again.status_code == 201 and again.json()["grant_id"] == person.json()["grant_id"]
    assert len([g for g in await _grants(tool.id) if g.state == "active"]) == 2

    events = await _events(server.id, "mcp_tool.block_updated")
    assert [(e.actor_user_id, e.outcome) for e in events[:2]] == [(TEST_USER_ID, "success")] * 2
    metadata = [e.event_metadata or {} for e in events]
    assert (metadata[0]["principal_type"], metadata[0]["principal_id"], metadata[0]["blocked"]) == (
        "user",
        str(OTHER_USER_ID),
        True,
    )
    assert (metadata[1]["principal_type"], metadata[1]["blocked"]) == ("group", True)

    lifted = await client.delete(f"{url}/{person.json()['grant_id']}")
    assert lifted.status_code == 204
    assert [b["principal_type"] for b in (await client.get(url)).json()["items"]] == ["group"]
    last = (await _events(server.id, "mcp_tool.block_updated"))[-1]
    assert (last.event_metadata or {})["blocked"] is False
    assert (await client.delete(f"{url}/{person.json()['grant_id']}")).status_code == 404
    assert (await client.delete(f"{url}/{uuid4()}")).status_code == 404


@pytest.mark.usefixtures("_manager")
async def test_the_owner_and_outsiders_cannot_be_blocked(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_outsider(db)
    server, tool = await add_server(db)
    url = _blocks(server, tool)
    foreign_team = await add_group(db, org=OTHER_ORG_ID)

    for body, reason in (
        ({"principal_type": "user", "principal_id": str(TEST_USER_ID)}, "owner"),
        ({"principal_type": "user", "principal_id": str(OUTSIDER_ID)}, None),
        ({"principal_type": "group", "principal_id": str(foreign_team)}, None),
        ({"principal_type": "user", "principal_id": str(uuid4())}, None),
    ):
        refused = await client.post(url, json=body)
        assert refused.status_code in (404, 422), refused.text
        if reason is not None:
            error = refused.json()["error"]
            assert (error["code"], error["details"]) == ("grant_invalid", {"reason": reason})
    organization = await client.post(
        url, json={"principal_type": "organization", "principal_id": str(TEST_ORG_ID)}
    )
    assert organization.status_code == 422
    assert await _grants(tool.id) == []
    assert await _events(server.id, "mcp_tool.block_updated") == []


@pytest.mark.usefixtures("_manager")
async def test_only_the_servers_manager_reads_and_writes_blocks(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    server, tool = await add_server(db)
    other_server, other_tool = await add_server(db, owner=OTHER_USER_ID, name="theirs")
    url = _blocks(server, tool)
    body = {"principal_type": "user", "principal_id": str(TEST_USER_ID)}
    standing = await client.post(
        url, json={"principal_type": "user", "principal_id": str(OTHER_USER_ID)}
    )

    with acting_as(test_app, ADMIN, TEST_ORG_ID):  # not this server's manager
        assert (await client.get(url)).status_code == 404
        assert (await client.post(url, json=body)).status_code == 404
        lift = await client.delete(f"{url}/{standing.json()['grant_id']}")
        assert lift.status_code == 404
    # A tool of another server under this server's address is unknown.
    crossed = await client.post(_blocks(server, other_tool), json=body)
    assert crossed.status_code == 404
    assert (await client.get(_blocks(other_server, other_tool))).status_code == 404
    assert [g.principal_id for g in await _grants(tool.id)] == [OTHER_USER_ID]
    assert await _grants(other_tool.id) == []


@pytest.mark.usefixtures("_manager")
async def test_a_blocked_persons_run_is_not_given_the_tool(
    client: AsyncClient, db: AsyncSession
) -> None:
    # In legacy mode only the owner runs with the server, so the team block is read for them.
    agent = await add_agent(db)
    server, tool = await add_server(db)
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=tool.id))
    await db.commit()
    group = await add_group(db, members=(TEST_USER_ID,))
    url = _blocks(server, tool)

    async def given() -> list[str]:
        configs = await chat_service.build_tools_config(
            await loaded(db, agent),
            context=unit_context(TEST_USER_ID),
            db=db,
            identity=identity(agent, TEST_USER_ID),
        )
        return [config["name"] for config in configs]

    someone_else = await client.post(
        url, json={"principal_type": "user", "principal_id": str(OTHER_USER_ID)}
    )
    assert someone_else.status_code == 201, someone_else.text
    assert await given() == [tool.name]  # another person's block is not mine
    block = await client.post(url, json={"principal_type": "group", "principal_id": str(group)})
    assert block.status_code == 201, block.text
    assert await given() == []
    await client.delete(f"{url}/{block.json()['grant_id']}")
    assert await given() == [tool.name]


# --- the call itself is refused --------------------------------------------------------------


async def test_why_a_call_is_refused(db: AsyncSession) -> None:
    await seed_scope(db)
    server, tool = await add_server(db)
    group = await add_group(db, members=(OTHER_USER_ID,))

    async def refusal(user: UUID = OTHER_USER_ID) -> str | None:
        await db.commit()
        return await tool_blocks.call_refusal(TEST_TENANT_ID, TEST_ORG_ID, user, tool.id)

    assert await refusal() is None
    assert (
        await tool_blocks.call_refusal(TEST_TENANT_ID, TEST_ORG_ID, OTHER_USER_ID, uuid4()) is None
    )

    tool.enabled = False
    assert await refusal() == "tool_disabled"
    tool.enabled, tool.review_state = True, "rejected"
    assert await refusal() == "tool_disabled"
    tool.review_state = "approved"

    server.registration_state = "rejected"
    assert await refusal() == "registration_not_approved"
    server.registration_state = "approved"
    server.registration_fingerprint = "0" * 64  # the connection changed since approval
    assert await refusal() == "registration_not_approved"
    server.registration_fingerprint = None

    grant = ResourceGrant(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type="mcp_tool",
        resource_id=tool.id,
        relation="blocked",
        principal_type="group",
        principal_id=group,
    )
    db.add(grant)
    assert await refusal() == "tool_blocked"
    assert await refusal(TEST_USER_ID) is None
    grant.state = "revoked"
    assert await refusal() is None
    # A share of the tool is not a block, and a block of another tool is not this one's.
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="mcp_tool",
            resource_id=tool.id,
            relation="viewer",
            principal_type="user",
            principal_id=OTHER_USER_ID,
        )
    )
    assert await refusal() is None


def _wrapped(server: McpServer, tool: McpTool, calls: list[str]) -> MCPToolWithRetry:
    @lc_tool
    def send(body: str) -> str:
        """Send."""
        calls.append(body)
        return "sent"

    return MCPToolWithRetry(
        send,
        decision_context=DecisionContext(
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            user_id=OTHER_USER_ID,
            agent_id=None,
            conversation_id=None,
            server_id=str(server.id),
            tool_id=str(tool.id),
            tool_name="send",
            reviewed_fingerprint=None,
            risk_level=None,
            gateway=False,
        ),
    )


async def _call_decisions(tool_id: UUID) -> list[tuple[str, str | None]]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "mcp.tool_call", AuditEvent.target_id == str(tool_id))
            .order_by(AuditEvent.created_at)
        )
        return [(row.outcome, row.reason_code) for row in rows]


async def test_a_call_under_way_is_refused_once_the_person_is_blocked(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    server, tool = await add_server(db)
    calls: list[str] = []
    wrapped = _wrapped(server, tool, calls)

    assert await wrapped.ainvoke({"body": "a"}) == "sent"
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="mcp_tool",
            resource_id=tool.id,
            relation="blocked",
            principal_type="user",
            principal_id=OTHER_USER_ID,
        )
    )
    await db.commit()
    refused = await wrapped.ainvoke({"body": "b"})

    assert calls == ["a"]
    assert "not available to you now" in str(refused)
    assert ("denied", "tool_blocked") in await _call_decisions(tool.id)


async def test_a_call_is_refused_when_the_check_itself_fails(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    server, tool = await add_server(db)
    calls: list[str] = []
    wrapped = _wrapped(server, tool, calls)

    async def broken(*_: Any) -> str | None:
        raise RuntimeError("database is away")

    monkeypatch.setattr(tool_blocks, "call_refusal", broken)
    refused = await wrapped.ainvoke({"body": "a"})

    assert calls == []
    assert "not available to you now" in str(refused)
    assert ("denied", "availability_unknown") in await _call_decisions(tool.id)


# --- the company catalog switch holds at run time ---------------------------------------------


def _catalog(*items: tuple[str, UUID]) -> dict[str, Any]:
    return {
        "catalog_policy": {
            "items": [{"type": kind, "id": str(item), "enabled": False} for kind, item in items]
        }
    }


def test_catalog_switches_become_tool_keys() -> None:
    tool_id, server_id, model_id = uuid4(), uuid4(), uuid4()
    values = _catalog(("tool", tool_id), ("mcp_server", server_id), ("model", model_id))
    values["catalog_policy"]["items"].append({"type": "tool", "id": str(uuid4()), "enabled": True})

    assert set(policy.catalog_off(values)) == {f"tool:{tool_id}", f"mcp_server:{server_id}"}
    assert set(policy.catalog_off(None)) == set()
    assert set(policy.catalog_off({"catalog_policy": "broken"})) == set()


async def test_a_tool_the_company_catalog_switched_off_is_off_in_every_run(
    db: AsyncSession,
) -> None:
    await seed_scope(db)
    tool_id, server_id = uuid4(), uuid4()
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None

    before = await policy.resolve_tool_risk(db, TEST_TENANT_ID, "standard")
    assert before is None or f"tool:{tool_id}" not in before.tools

    tenant.settings = {
        **(tenant.settings or {}),
        **_catalog(("tool", tool_id), ("mcp_server", server_id)),
    }
    await db.commit()
    resolved = await policy.resolve_tool_risk(db, TEST_TENANT_ID, "standard")
    assert resolved is not None

    assert resolved.tools[f"tool:{tool_id}"].enabled is False
    assert resolved.tools[f"mcp_server:{server_id}"].enabled is False
