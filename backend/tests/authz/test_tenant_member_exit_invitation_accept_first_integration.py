"""PostgreSQL accept-first invitation race against company member exit."""

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

from app.authz import invitation_queries
from app.authz.error_codes import ErrorCode
from app.authz.tenant_org_write_locks import TenantOrgWriteLock
from app.models.organization import OrganizationMember, TenantMember, TenantRoleGrant
from app.models.organization_group import OrgInvitation
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_acceptance import InvitationAcceptance, accept_invitation
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.tenant_member_exit_native_diagnostics import (
    NativeInvitationAcceptCommitStageError,
    NativeInvitationAcceptResumeStageError,
    NativeInvitationAcceptServiceStageError,
    NativeInvitationBarrierTimeoutStageError,
    NativeInvitationBarrierUnknownStageError,
    NativeInvitationCleanupStageError,
    NativeInvitationCompanyStateStageError,
    NativeInvitationFailurePhase,
    NativeInvitationHistoryStateStageError,
    NativeInvitationOrganizationStateStageError,
    NativeInvitationRemovalCommitStageError,
    NativeInvitationRemovalServiceStageError,
    NativeInvitationTenantRoleStateStageError,
    _native_invitation_failure,
)
from tests.authz.tenant_member_exit_native_fixture import (
    audit_request,
    cleanup_company,
    seed_company,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_member_exit_native_fixture"]


async def test_company_exit_serializes_with_org_invitation_acceptance(
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
    original = invitation_queries.lock_tenant_org_write
    locked, release = anyio.Event(), anyio.Event()
    accept_resumed = False
    accept_service_returned = False
    accept_committed = False
    removal_service_returned = False
    removal_committed = False
    origin_error: AssertionError | None = None

    def capture_origin(phase: NativeInvitationFailurePhase, error: Exception) -> AssertionError:
        nonlocal origin_error
        diagnostic = _native_invitation_failure(phase, error)
        if origin_error is None:
            origin_error = diagnostic
        return diagnostic

    async def hold_accept_lock(
        db: AsyncSession,
        tenant_id: UUID,
        organization_ids: Collection[UUID] | None,
        missing_code: ErrorCode = "not_found",
    ) -> TenantOrgWriteLock:
        nonlocal accept_resumed
        result = await original(db, tenant_id, organization_ids, missing_code)
        locked.set()
        await release.wait()
        accept_resumed = True
        return result

    monkeypatch.setattr(invitation_queries, "lock_tenant_org_write", hold_accept_lock)
    call_phase_complete = False

    async def accept() -> None:
        nonlocal accept_service_returned, accept_committed
        async with native_sessions() as db:
            mutation = OrgMutationRequest(db, audit_request(), None)
            try:
                await accept_invitation(
                    InvitationAcceptance(mutation, fixture.target_id, SecretStr(token))
                )
            except Exception as error:
                raise capture_origin("AcceptService", error) from None
            accept_service_returned = True
            try:
                await db.commit()
            except Exception as error:
                raise capture_origin("AcceptCommit", error) from None
            accept_committed = True

    async def remove() -> None:
        nonlocal removal_service_returned, removal_committed
        async with native_sessions() as db:
            try:
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            except Exception as error:
                raise capture_origin("RemovalService", error) from None
            removal_service_returned = True
            try:
                await db.commit()
            except Exception as error:
                raise capture_origin("RemovalCommit", error) from None
            removal_committed = True

    try:
        try:
            with anyio.fail_after(10):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(accept)
                    await locked.wait()
                    tasks.start_soon(remove)
                    await anyio.sleep(0.1)
                    release.set()
        except TimeoutError:
            raise NativeInvitationBarrierTimeoutStageError from None
        except BaseExceptionGroup:
            if origin_error is not None:
                raise origin_error from None
            if not accept_resumed:
                raise NativeInvitationAcceptResumeStageError from None
            if not accept_service_returned:
                raise NativeInvitationAcceptServiceStageError from None
            if not accept_committed:
                raise NativeInvitationAcceptCommitStageError from None
            if not removal_service_returned:
                raise NativeInvitationRemovalServiceStageError from None
            if not removal_committed:
                raise NativeInvitationRemovalCommitStageError from None
            raise NativeInvitationBarrierUnknownStageError from None
        async with native_sessions() as db:
            company = await db.get(TenantMember, (fixture.tenant_id, fixture.target_id))
            if company is None or company.status != "removed":
                raise NativeInvitationCompanyStateStageError
            active_orgs = await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(
                    OrganizationMember.tenant_id == fixture.tenant_id,
                    OrganizationMember.user_id == fixture.target_id,
                    OrganizationMember.status == "active",
                )
            )
            if active_orgs != 0:
                raise NativeInvitationOrganizationStateStageError
            roles = await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(
                    TenantRoleGrant.tenant_id == fixture.tenant_id,
                    TenantRoleGrant.user_id == fixture.target_id,
                )
            )
            if roles != 0:
                raise NativeInvitationTenantRoleStateStageError
            persisted = await db.get(OrgInvitation, invitation_id)
            if persisted is None or persisted.accepted_at is None:
                raise NativeInvitationHistoryStateStageError
        call_phase_complete = True
    finally:
        try:
            await cleanup_company(
                native_sessions,
                fixture,
                call_phase_complete=call_phase_complete,
            )
        except AssertionError:
            raise NativeInvitationCleanupStageError from None
