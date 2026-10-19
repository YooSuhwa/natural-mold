"""Deterministic archive ordering/outcome tests on the shared aiosqlite engine (I22).

aiosqlite shares one connection, so true row-lock barriers are impossible here:
each race is proven by running both winner orders sequentially and checking
the exact outcome, plus a runtime trace of every canonical locking statement.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import Select, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from app.auth.jwt import create_org_invitation_token
from app.authz.execution_transaction_tracker import CANONICAL_LOCK_TABLES
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import Organization, OrganizationMember
from app.models.organization_group import OrgInvitation
from app.schemas.agent import AgentCreate
from app.services import agent_lifecycle_locks
from app.services.agent_creation_service import create_agent
from app.services.agent_lifecycle import archive_agent
from app.services.chat.chat_execution_preparation import create_conversation
from app.services.orgs.versioning import etag
from app.services.tenants import organization_archive_enumeration as enumeration
from app.services.tenants.organization_archive_enumeration import ArchiveScope
from tests.authz.organization_archive_workload import Workload, seed_workload
from tests.authz.tenant_organizations_fixture import Task7Case, seed_task7_case
from tests.conftest import AuthSession, register_session

pytest_plugins = [
    "tests.authz.tenant_organizations_fixture",
    "tests.authz.tenant_organization_lifecycle_app",
]
LIFECYCLE = "agent_lifecycle"


@asynccontextmanager
async def lock_trace(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[str]]:
    """Record every canonical-table locking statement and lifecycle lock, in order."""
    trace: list[str] = []
    patched = agent_lifecycle_locks.acquire_agent_lifecycle_locks

    async def lifecycle(db: AsyncSession, agent_ids: Iterable[UUID]) -> None:
        ids = tuple(agent_ids)
        assert list(ids) == sorted(ids, key=lambda value: value.bytes)
        trace.append(LIFECYCLE)
        await patched(db, ids)

    def on_execute(state: ORMExecuteState) -> None:
        statement = state.statement
        if not isinstance(statement, Select) or statement._for_update_arg is None:
            return
        names = {getattr(item, "name", None) for item in statement.get_final_froms()}
        trace.extend(sorted(name for name in names & CANONICAL_LOCK_TABLES if name))

    monkeypatch.setattr(agent_lifecycle_locks, "acquire_agent_lifecycle_locks", lifecycle)
    event.listen(Session, "do_orm_execute", on_execute)
    try:
        yield trace
    finally:
        event.remove(Session, "do_orm_execute", on_execute)


def _collapse(trace: list[str]) -> list[str]:
    return [name for index, name in enumerate(trace) if index == 0 or trace[index - 1] != name]


async def _org(db: AsyncSession, org_id: UUID) -> Organization:
    db.expire_all()
    row = await db.get(Organization, org_id)
    assert row is not None
    return row


async def _case(db: AsyncSession, client: AsyncClient) -> tuple[Task7Case, Workload]:
    case = await seed_task7_case(client, db)
    db.add(Organization(tenant_id=case.tenant_id, name="Sibling", slug=f"sib-{uuid4().hex}"))
    await db.commit()
    workload = await seed_workload(
        db, tenant_id=case.tenant_id, org_id=case.org_id, user_id=case.target_id
    )
    return case, workload


async def _lifecycle(db: AsyncSession, client: AsyncClient, case: Task7Case, action: str):
    case.auth.apply(client)
    version = etag((await _org(db, case.org_id)).updated_at)
    return await client.post(
        f"/api/tenants/{case.tenant_id}/organizations/{case.org_id}/{action}",
        json={"confirm_name": "Existing organization"} if action == "archive" else None,
        headers={**case.auth.headers(), "If-Match": version},
    )


def _context(case: Task7Case) -> OrgContext:
    return OrgContext(org_id=case.org_id, tenant_id=case.tenant_id, user_id=case.target_id)


async def _count(db: AsyncSession, model: type) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_archive_lock_trace_follows_canonical_order(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, _ = await _case(db, lifecycle_client)
    async with lock_trace(monkeypatch) as trace:
        response = await _lifecycle(db, lifecycle_client, case, "archive")
    assert response.status_code == 200
    assert _collapse(trace) == [
        "tenants",
        "organizations",
        LIFECYCLE,
        "agent_triggers",
        "agent_deployments",
        "conversations",
        "conversation_runs",
        "conversation_run_inputs",
        "agent_api_runs",
        "agent_trigger_runs",
    ]


async def test_creation_after_archive_fails_unavailable_without_row_delta(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _case(db, lifecycle_client)
    seeded = await db.get(Agent, w.agent_id)
    assert seeded is not None
    model_id = seeded.model_id
    early = await create_agent(
        db, AgentCreate(name="Early", system_prompt="p", model_id=model_id), _context(case)
    )
    await db.commit()
    early_id = early.id
    scopes: list[ArchiveScope] = []
    original = enumeration.lock_archive_scope

    async def spy(session: AsyncSession, organization: Organization) -> ArchiveScope:
        scopes.append(scope := await original(session, organization))
        return scope

    monkeypatch.setattr(enumeration, "lock_archive_scope", spy)
    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200
    assert early_id in scopes[0].agent_ids
    kept = await db.get(Agent, early_id)
    assert kept is not None and kept.archived_at is None
    counts = [await _count(db, model) for model in (Agent, Conversation, AuditEvent)]

    with pytest.raises(AppError) as agent_denied:
        await create_agent(
            db, AgentCreate(name="Late", system_prompt="p", model_id=model_id), _context(case)
        )
    await db.rollback()
    with pytest.raises(AppError) as conversation_denied:
        await create_conversation(db, early_id, context=_context(case))
    await db.rollback()

    assert agent_denied.value.code == conversation_denied.value.code == "organization_unavailable"
    assert [await _count(db, model) for model in (Agent, Conversation, AuditEvent)] == counts


async def _invite(db: AsyncSession, case: Task7Case) -> tuple[UUID, str, str]:
    email = f"invitee-{uuid4().hex}@example.com"
    invitation_id = uuid4()
    token = create_org_invitation_token(invitation_id)
    now = datetime.now(UTC)
    db.add(
        OrgInvitation(
            id=invitation_id,
            org_id=case.org_id,
            tenant_id=case.tenant_id,
            email=email,
            role="member",
            group_ids=[],
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    await db.commit()
    return invitation_id, token, email


async def _accept(client: AsyncClient, session: AuthSession, token: str) -> Response:
    session.apply(client)
    return await client.post(
        "/api/invitations/accept", json={"token": token}, headers=session.headers()
    )


async def _membership(db: AsyncSession, org_id: UUID, email: str) -> OrganizationMember | None:
    db.expire_all()
    return await db.scalar(
        select(OrganizationMember)
        .join(OrgInvitation, OrgInvitation.org_id == OrganizationMember.org_id)
        .where(OrganizationMember.org_id == org_id, OrgInvitation.email == email)
        .where(OrganizationMember.user_id == OrgInvitation.accepted_by)
    )


async def test_invite_accept_versus_archive_both_orders(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case, _ = await _case(db, lifecycle_client)
    invitation_id, token, email = await _invite(db, case)
    invitee = await register_session(lifecycle_client, email=email, name="Invitee")

    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200
    audits = await _count(db, AuditEvent)
    denied = await _accept(lifecycle_client, invitee, token)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "organization_unavailable"
    db.expire_all()
    pending = await db.get(OrgInvitation, invitation_id)
    assert pending is not None and pending.accepted_at is None
    assert await _membership(db, case.org_id, email) is None
    assert await _count(db, AuditEvent) == audits

    assert (await _lifecycle(db, lifecycle_client, case, "restore")).status_code == 200
    accepted = await _accept(lifecycle_client, invitee, token)
    assert accepted.status_code == 200
    member = await _membership(db, case.org_id, email)
    assert member is not None and member.status == "active"

    # Accept-first: the accepted invite and active membership survive a later archive.
    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200
    survivor = await _membership(db, case.org_id, email)
    assert survivor is not None and survivor.status == "active"
    assert (await _org(db, case.org_id)).status == "archived"


async def test_b08_compatibility_lock_race_has_no_deadlock(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _case(db, lifecycle_client)
    actor = CurrentUser(id=case.target_id, email="target@example.com", name="Target")
    async with lock_trace(monkeypatch) as b08_trace:
        await asyncio.wait_for(archive_agent(db, actor, w.agent_id), timeout=10)
        await db.commit()
    # Standalone B08 order: lifecycle -> Agent -> Conversation -> Trigger, no parent lock.
    assert _collapse(b08_trace) == [LIFECYCLE, "agents", "conversations", "agent_triggers"]
    db.expire_all()
    canceled = await db.get(ConversationRunInput, w.pending_input_id)
    assert canceled is not None and (canceled.status, canceled.revision) == ("canceled", 2)

    archived = await asyncio.wait_for(_lifecycle(db, lifecycle_client, case, "archive"), timeout=10)
    assert archived.status_code == 200
    db.expire_all()
    again = await db.get(ConversationRunInput, w.pending_input_id)
    assert again is not None and (again.status, again.revision) == ("canceled", 2)

    # Organization archive first: a later B08 agent archive terminates with a typed denial.
    with pytest.raises(AppError) as denied:
        await asyncio.wait_for(archive_agent(db, actor, w.idle_agent_id), timeout=10)
    await db.rollback()
    assert denied.value.code == "AGENT_NOT_FOUND"
    assert (await _org(db, case.org_id)).status == "archived"
    idle = await db.get(Agent, w.idle_agent_id)
    assert idle is not None and idle.archived_at is None
