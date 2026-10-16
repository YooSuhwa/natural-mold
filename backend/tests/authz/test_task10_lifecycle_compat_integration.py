"""Native PostgreSQL lifecycle compatibility barriers for organization archive (I23).

Organization archive versus the standalone B08 agent archive (both winner
orders, no deadlock), Agent/Conversation creation versus archive (an open
creating transaction blocks archive; a parked archive blocks creation, which
then fails ``organization_unavailable`` with zero row delta), invitation
accept versus archive (archive-first leaves the invite pending until restore;
accept-first membership survives archive), and restore never replaying work
or re-enabling triggers/deployments.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.auth.jwt import create_access_token, create_csrf_token, create_org_invitation_token
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.models.organization_group import OrgInvitation
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.services.agent_creation_service import create_agent
from app.services.agent_lifecycle import archive_agent
from app.services.chat.chat_execution_preparation import create_conversation
from app.services.tenants import organization_archive_enumeration as enumeration
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.authz.organization_archive_workload import seed_workload
from tests.authz.task10_runtime_barriers import assert_blocked, finish, park_after
from tests.authz.test_tenant_org_lifecycle_wiring import _snapshot

if TYPE_CHECKING:
    from tests.authz.task10_native_fixture import Lane

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.task10_native_fixture"]


async def _graph(lane: Lane) -> GatewayGraph:
    async with lane.sessions() as db:
        return await seed_graph(db, conversations=1)


async def _count(lane: Lane, model: Any, tenant_id: UUID) -> int:
    async with lane.sessions() as db:
        statement = select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        return int(await db.scalar(statement) or 0)


async def _in_session(lane: Lane, work: Any) -> Any:
    async with lane.sessions() as db:
        try:
            result = await work(db)
        except AppError as error:
            await db.rollback()
            return error.code
        await db.commit()
        return result


@pytest.mark.parametrize("winner", ["organization", "b08"])
async def test_native_org_archive_versus_b08_agent_archive_without_deadlock(
    task10_lane: Lane, winner: str
) -> None:
    graph = await _graph(task10_lane)
    admin = await task10_lane.admin(graph.context)
    actor = CurrentUser(id=graph.context.user_id, email="owner@example.com", name="Owner")
    if winner == "organization":
        gate = park_after(task10_lane.monkeypatch, enumeration, "lock_archive_scope")
        archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
        await gate.wait_entered()
        b08 = asyncio.create_task(
            _in_session(task10_lane, lambda db: archive_agent(db, actor, graph.agent_id))
        )
        await assert_blocked(b08)
        gate.release.set()
        assert (await finish(archive)).status_code == 200
        assert await finish(b08) == "AGENT_NOT_FOUND"
    else:
        async with task10_lane.sessions() as holder:
            await archive_agent(holder, actor, graph.agent_id)
            archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
            await assert_blocked(archive)
            await holder.commit()
        assert (await finish(archive)).status_code == 200
    async with task10_lane.sessions() as db:
        agent = await db.get(Agent, graph.agent_id)
    assert agent is not None
    assert (agent.archived_at is not None) is (winner == "b08")


def _creator(kind: str, graph: GatewayGraph, model_id: UUID) -> Any:
    if kind == "agent":
        payload = AgentCreate(name=f"Racer {uuid4().hex[:6]}", system_prompt="p", model_id=model_id)
        return lambda db: create_agent(db, payload, graph.context)
    return lambda db: create_conversation(db, graph.agent_id, context=graph.context)


@pytest.mark.parametrize("kind", ["agent", "conversation"])
@pytest.mark.parametrize("winner", ["create", "archive"])
async def test_native_creation_versus_archive_has_one_bounded_winner(
    task10_lane: Lane, kind: str, winner: str
) -> None:
    graph = await _graph(task10_lane)
    admin = await task10_lane.admin(graph.context)
    async with task10_lane.sessions() as db:
        agent = await db.get(Agent, graph.agent_id)
        assert agent is not None
        model_id = agent.model_id
    create, model = _creator(kind, graph, model_id), Agent if kind == "agent" else Conversation
    before = await _count(task10_lane, model, graph.context.tenant_id)
    if winner == "create":
        async with task10_lane.sessions() as holder:
            await create(holder)
            archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
            await assert_blocked(archive)
            await holder.commit()
        assert (await finish(archive)).status_code == 200
        assert await _count(task10_lane, model, graph.context.tenant_id) == before + 1
        return
    gate = park_after(task10_lane.monkeypatch, enumeration, "lock_archive_scope")
    archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
    await gate.wait_entered()
    racer = asyncio.create_task(_in_session(task10_lane, create))
    await assert_blocked(racer)
    gate.release.set()
    assert (await finish(archive)).status_code == 200
    assert await finish(racer) == "organization_unavailable"
    assert await _count(task10_lane, model, graph.context.tenant_id) == before


async def _invitation(lane: Lane, graph: GatewayGraph) -> tuple[UUID, str, User]:
    invitation_id, key = uuid4(), uuid4().hex
    token = create_org_invitation_token(invitation_id)
    invitee = User(email=f"task10-invitee-{key}@example.com", name="Invitee", is_active=True)
    now = datetime.now(UTC)
    async with lane.sessions() as db:
        db.add(invitee)
        db.add(
            OrgInvitation(
                id=invitation_id,
                org_id=graph.context.org_id,
                tenant_id=graph.context.tenant_id,
                email=invitee.email,
                role="member",
                group_ids=[],
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                created_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        await db.commit()
    return invitation_id, token, invitee


async def _accept(lane: Lane, invitee: User, token: str) -> Any:
    csrf = create_csrf_token(invitee.id)
    lane.client.cookies.clear()
    lane.client.cookies.set(settings.cookie_name_access, create_access_token(invitee.id))
    lane.client.cookies.set(settings.cookie_name_csrf, csrf)
    return await lane.client.post(
        "/api/invitations/accept", json={"token": token}, headers={"X-CSRF-Token": csrf}
    )


async def _membership(lane: Lane, graph: GatewayGraph, user_id: UUID) -> str | None:
    async with lane.sessions() as db:
        row = await db.get(OrganizationMember, (graph.context.org_id, user_id))
    return None if row is None else row.status


async def test_native_invite_accept_versus_archive_both_orders(task10_lane: Lane) -> None:
    graph = await _graph(task10_lane)
    admin = await task10_lane.admin(graph.context)
    invitation_id, token, invitee = await _invitation(task10_lane, graph)
    gate = park_after(task10_lane.monkeypatch, enumeration, "lock_archive_scope")

    archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
    await gate.wait_entered()
    accept = asyncio.create_task(_accept(task10_lane, invitee, token))
    await assert_blocked(accept)
    gate.release.set()
    archived, denied = await finish(archive), await finish(accept)

    assert archived.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "organization_unavailable"
    async with task10_lane.sessions() as db:
        pending = await db.get(OrgInvitation, invitation_id)
    assert pending is not None and pending.accepted_at is None
    assert await _membership(task10_lane, graph, invitee.id) is None

    restored = await task10_lane.lifecycle(admin, "restore", version=archived.headers["etag"])
    assert restored.status_code == 200
    assert (await _accept(task10_lane, invitee, token)).status_code == 200
    assert await _membership(task10_lane, graph, invitee.id) == "active"
    # Accept-first: the accepted membership survives a later archive.
    assert (await task10_lane.lifecycle(admin, "archive")).status_code == 200
    assert await _membership(task10_lane, graph, invitee.id) == "active"


async def test_native_restore_never_replays_or_reenables(task10_lane: Lane) -> None:
    graph = await _graph(task10_lane)
    admin = await task10_lane.admin(graph.context)
    context = graph.context
    async with task10_lane.sessions() as db:
        workload = await seed_workload(
            db, tenant_id=context.tenant_id, org_id=context.org_id, user_id=context.user_id
        )
    archived = await task10_lane.lifecycle(admin, "archive")
    assert archived.status_code == 200
    async with task10_lane.sessions() as db:
        after_archive = await _snapshot(db, workload)

    restored = await task10_lane.lifecycle(admin, "restore", version=archived.headers["etag"])

    assert restored.status_code == 200 and restored.json()["status"] == "active"
    async with task10_lane.sessions() as db:
        assert await _snapshot(db, workload) == after_archive
    calls = task10_lane.scheduler_calls
    assert calls["add_trigger_job"] == calls["resume_trigger_job"] == []
    assert calls["remove_trigger_job"] == [(workload.trigger_id,)]
