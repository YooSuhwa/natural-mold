"""PostgreSQL remove-first invitation race against company member exit."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz.tenant_org_write_locks import TenantOrgWriteLock
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization_group import OrgInvitation
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_acceptance import InvitationAcceptance, accept_invitation
from app.services.tenants import member_exit
from tests.authz.tenant_member_exit_native_fixture import (
    assert_removed,
    audit_request,
    cleanup_company,
    remove_company,
    seed_company,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_member_exit_native_fixture"]


async def test_company_exit_wins_before_org_invitation_acceptance(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db, org_count=2, membership_count=1)
        token = f"native-invite-{uuid4().hex}"
        invitation = OrgInvitation(
            org_id=fixture.organizations[1],
            tenant_id=fixture.tenant_id,
            email=fixture.target_email,
            role="member",
            group_ids=[],
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            invited_by=fixture.actor.id,
        )
        db.add(invitation)
        await db.commit()
        invitation_id = invitation.id
    original = member_exit.lock_tenant_org_write
    locked, release = anyio.Event(), anyio.Event()
    acceptance_error: list[tuple[str, int]] = []

    async def hold_removal_lock(
        db: AsyncSession, tenant_id: UUID, organization_ids: Collection[UUID] | None
    ) -> TenantOrgWriteLock:
        result = await original(db, tenant_id, organization_ids)
        locked.set()
        await release.wait()
        return result

    monkeypatch.setattr(member_exit, "lock_tenant_org_write", hold_removal_lock)
    call_phase_complete = False

    async def accept() -> None:
        async with native_sessions() as db:
            mutation = OrgMutationRequest(db, audit_request(), None)
            try:
                await accept_invitation(
                    InvitationAcceptance(mutation, fixture.target_id, SecretStr(token))
                )
            except AppError as error:
                acceptance_error.append((error.code, error.status))
                await db.rollback()

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(remove_company, native_sessions, fixture)
                await locked.wait()
                tasks.start_soon(accept)
                await anyio.sleep(0.1)
                release.set()
        assert acceptance_error == [("invitation_membership_blocked", 403)]
        await assert_removed(native_sessions, fixture)
        async with native_sessions() as db:
            persisted = await db.get(OrgInvitation, invitation_id)
            assert persisted is not None and persisted.accepted_at is None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(invitation_id),
                        AuditEvent.action == "org.invitation_accept",
                    )
                )
                == 0
            )
        call_phase_complete = True
    finally:
        await cleanup_company(
            native_sessions,
            fixture,
            call_phase_complete=call_phase_complete,
        )
