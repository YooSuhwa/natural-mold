"""PostgreSQL barriers for company exit against existing membership writers."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from unittest.mock import Mock
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz import invitation_queries, org_control
from app.authz.error_codes import ErrorCode
from app.authz.org_control import OrgControlContext
from app.authz.org_member_exit import MemberExitPlan
from app.authz.tenant_org_write_locks import TenantOrgWriteLock
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import OrgInvitation
from app.models.skill import Skill
from app.models.user import User
from app.schemas.org_members import MemberCreate
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_acceptance import InvitationAcceptance, accept_invitation
from app.services.orgs.members import create_member
from app.services.orgs.versioning import etag
from app.services.tenants import member_exit
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.company_administration_migration_fixture import _runner_dsns

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class NativeCompany:
    tenant_id: UUID
    actor: CurrentUser
    target_id: UUID
    target_email: str
    organizations: tuple[UUID, ...]


class NativeInvitationAcceptResumeStageError(AssertionError):
    pass


class NativeInvitationAcceptServiceStageError(AssertionError):
    pass


class NativeInvitationAcceptCommitStageError(AssertionError):
    pass


class NativeInvitationRemovalServiceStageError(AssertionError):
    pass


class NativeInvitationRemovalCommitStageError(AssertionError):
    pass


class NativeInvitationBarrierTimeoutStageError(AssertionError):
    pass


class NativeInvitationBarrierUnknownStageError(AssertionError):
    pass


class NativeInvitationCompanyStateStageError(AssertionError):
    pass


class NativeInvitationOrganizationStateStageError(AssertionError):
    pass


class NativeInvitationTenantRoleStateStageError(AssertionError):
    pass


class NativeInvitationHistoryStateStageError(AssertionError):
    pass


class NativeInvitationCleanupStageError(AssertionError):
    pass


type NativeInvitationFailurePhase = Literal[
    "AcceptService",
    "AcceptCommit",
    "RemovalService",
    "RemovalCommit",
]

_APP_ERROR_TYPE_LABELS: Final = {
    "AppError": "AppError",
    "NotFoundError": "NotFoundError",
    "ValidationError": "ValidationError",
    "ConflictError": "ConflictError",
    "ForbiddenError": "ForbiddenError",
    "ExternalServiceError": "ExternalServiceError",
}
_APP_ERROR_CODE_LABELS: Final = {
    "not_found": "NotFound",
    "permission_required": "PermissionRequired",
    "organization_unavailable": "OrganizationUnavailable",
    "policy_locked": "PolicyLocked",
    "last_admin": "LastAdmin",
    "resource_transfer_required": "ResourceTransferRequired",
    "invitation_invalid": "InvitationInvalid",
    "invitation_used": "InvitationUsed",
    "invitation_expired": "InvitationExpired",
    "invitation_email_mismatch": "InvitationEmailMismatch",
    "invitation_membership_blocked": "InvitationMembershipBlocked",
    "invitation_group_unavailable": "InvitationGroupUnavailable",
    "last_tenant_admin": "LastTenantAdmin",
    "tenant_suspended": "TenantSuspended",
    "tenant_unavailable": "TenantUnavailable",
}
_DBAPI_SQLSTATE_LABELS: Final = {
    "23503": "Sqlstate23503",
    "23505": "Sqlstate23505",
    "40P01": "Sqlstate40P01",
    "55P03": "Sqlstate55P03",
    "57014": "Sqlstate57014",
}
_STANDARD_EXCEPTION_TYPE_LABELS: Final = {
    "AssertionError": "AssertionError",
    "InvalidRequestError": "InvalidRequestError",
    "LookupError": "LookupError",
    "RuntimeError": "RuntimeError",
    "TypeError": "TypeError",
    "ValueError": "ValueError",
}


def _native_invitation_cause_label(error: Exception) -> str:
    if isinstance(error, AppError):
        error_type = _APP_ERROR_TYPE_LABELS.get(type(error).__name__, "UnknownAppError")
        error_code = _APP_ERROR_CODE_LABELS.get(error.code, "UnknownCode")
        return f"{error_type}{error_code}"
    if isinstance(error, DBAPIError):
        if isinstance(error, IntegrityError):
            error_type = "IntegrityError"
        elif isinstance(error, OperationalError):
            error_type = "OperationalError"
        else:
            error_type = "DBAPIError"
        raw_sqlstate = getattr(error.orig, "sqlstate", None)
        sqlstate = (
            _DBAPI_SQLSTATE_LABELS.get(raw_sqlstate, "UnknownSqlstate")
            if isinstance(raw_sqlstate, str)
            else "UnknownSqlstate"
        )
        return f"{error_type}{sqlstate}"
    if isinstance(error, InvalidRequestError):
        return "InvalidRequestError"
    return _STANDARD_EXCEPTION_TYPE_LABELS.get(type(error).__name__, "UnexpectedError")


def _native_invitation_failure(
    phase: NativeInvitationFailurePhase, error: Exception
) -> AssertionError:
    cause = _native_invitation_cause_label(error)
    diagnostic_type = type(
        f"NativeInvitation{phase}{cause}DiagnosticError",
        (AssertionError,),
        {"__module__": __name__},
    )
    return diagnostic_type()


def audit_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/native-fixture",
            "raw_path": b"/native-fixture",
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": ("test", 80),
            "state": {},
        }
    )


async def seed_company(
    db: AsyncSession, *, org_count: int = 3, membership_count: int | None = None
) -> NativeCompany:
    key = uuid4().hex
    actor = User(email=f"native-admin-{key}@example.test", name="Native admin")
    target = User(email=f"native-target-{key}@example.test", name="Native target")
    tenant = Tenant(name="Native company", slug=f"native-{key}", secrets_namespace=f"native/{key}")
    db.add_all([actor, target, tenant])
    await db.flush()
    organizations = tuple(
        Organization(tenant_id=tenant.id, name=f"Native {index}", slug=f"native-{key}-{index}")
        for index in range(org_count)
    )
    db.add_all(organizations)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=tenant.id, user_id=actor.id),
            TenantMember(tenant_id=tenant.id, user_id=target.id),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=actor.id,
                role="admin",
                granted_by=actor.id,
            ),
        ]
    )
    db.add_all(
        OrganizationMember(org_id=organization.id, tenant_id=tenant.id, user_id=target.id)
        for organization in organizations[: membership_count or org_count]
    )
    await db.commit()
    return NativeCompany(
        tenant.id,
        CurrentUser(id=actor.id, email=actor.email, name=actor.name, is_super_user=False),
        target.id,
        target.email,
        tuple(organization.id for organization in organizations),
    )


async def remove_company(
    sessions: async_sessionmaker[AsyncSession], fixture: NativeCompany
) -> None:
    async with sessions() as db:
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
        )
        await db.commit()


async def assert_removed(
    sessions: async_sessionmaker[AsyncSession], fixture: NativeCompany
) -> None:
    async with sessions() as db:
        company = await db.get(TenantMember, (fixture.tenant_id, fixture.target_id))
        active_orgs = await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.tenant_id == fixture.tenant_id,
                OrganizationMember.user_id == fixture.target_id,
                OrganizationMember.status == "active",
            )
        )
        roles = await db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == fixture.tenant_id,
                TenantRoleGrant.user_id == fixture.target_id,
            )
        )
        assert company is not None and company.status == "removed"
        assert active_orgs == 0
        assert roles == 0


async def cleanup_company(
    sessions: async_sessionmaker[AsyncSession],
    fixture: NativeCompany,
    *,
    call_phase_complete: bool | None = None,
) -> None:
    try:
        async with sessions() as db:
            await db.execute(delete(Skill).where(Skill.tenant_id == fixture.tenant_id))
            await db.execute(
                delete(OrgInvitation).where(OrgInvitation.tenant_id == fixture.tenant_id)
            )
            await db.execute(
                delete(Organization).where(Organization.tenant_id == fixture.tenant_id)
            )
            await db.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
            await db.execute(delete(User).where(User.id.in_((fixture.actor.id, fixture.target_id))))
            await db.commit()
    except IntegrityError as error:
        raise AssertionError(
            "native_company_fixture_cleanup_integrity_error:"
            f"call_phase_complete={call_phase_complete}"
        ) from error


@pytest.fixture
async def native_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_runner_dsns().async_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_native_valid_company_exit_removes_all_organizations(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    try:
        await remove_company(native_sessions, fixture)
        await assert_removed(native_sessions, fixture)
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_late_ownership_conflict_leaves_company_memberships_unchanged(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
        db.add(
            Skill(
                user_id=fixture.target_id,
                name="Native owned skill",
                slug=f"native-owned-{uuid4().hex}",
                tenant_id=fixture.tenant_id,
                org_id=sorted(fixture.organizations, key=lambda value: value.bytes)[-1],
            )
        )
        await db.commit()
    try:
        async with native_sessions() as db:
            with pytest.raises(AppError) as captured:
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            assert captured.value.code == "resource_transfer_required"
            assert captured.value.status == 409
            await db.rollback()
        async with native_sessions() as db:
            statuses = list(
                await db.scalars(
                    select(OrganizationMember.status).where(
                        OrganizationMember.tenant_id == fixture.tenant_id,
                        OrganizationMember.user_id == fixture.target_id,
                    )
                )
            )
            assert len(statuses) == 3
            assert set(statuses) == {"active"}
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action.in_(("org.member_remove", "tenant.member_remove")),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_late_apply_failure_rolls_back_every_company_exit_write(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    original = member_exit.apply_member_exit
    calls = 0

    async def fail_second(db: AsyncSession, plan: MemberExitPlan) -> None:
        nonlocal calls
        calls += 1
        await original(db, plan)
        if calls == 2:
            raise RuntimeError("native injected late failure")

    monkeypatch.setattr(member_exit, "apply_member_exit", fail_second)
    try:
        async with native_sessions() as db:
            with pytest.raises(RuntimeError, match="native injected late failure"):
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            await db.rollback()
        async with native_sessions() as db:
            statuses = list(
                await db.scalars(
                    select(OrganizationMember.status).where(
                        OrganizationMember.tenant_id == fixture.tenant_id,
                        OrganizationMember.user_id == fixture.target_id,
                    )
                )
            )
            assert len(statuses) == 3
            assert set(statuses) == {"active"}
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_company_exit_serializes_with_add_in_initially_unaffected_organization(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db, org_count=2, membership_count=1)
    organization_id = fixture.organizations[1]
    original = org_control.lock_tenant_org_write
    locked, release, add_done = anyio.Event(), anyio.Event(), anyio.Event()

    async def hold_add_lock(
        db: AsyncSession, tenant_id: UUID, organization_ids: Collection[UUID] | None
    ) -> TenantOrgWriteLock:
        result = await original(db, tenant_id, organization_ids)
        locked.set()
        await release.wait()
        return result

    monkeypatch.setattr(org_control, "lock_tenant_org_write", hold_add_lock)

    async def add_member() -> None:
        async with native_sessions() as db:
            organization = await db.get(Organization, organization_id)
            assert organization is not None
            context = OrgControlContext(organization, fixture.actor, ("can_manage_members",))
            mutation = OrgMutationRequest(db, audit_request(), etag(organization.updated_at))
            await create_member(mutation, context, MemberCreate(user_id=fixture.target_id))
        add_done.set()

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(add_member)
                await locked.wait()
                tasks.start_soon(remove_company, native_sessions, fixture)
                await anyio.sleep(0.1)
                assert not add_done.is_set()
                release.set()
        await assert_removed(native_sessions, fixture)
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_company_exit_wins_before_add_in_initially_unaffected_organization(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db, org_count=2, membership_count=1)
    organization_id = fixture.organizations[1]
    original = member_exit.lock_tenant_org_write
    locked, release = anyio.Event(), anyio.Event()
    add_error: list[str] = []

    async def hold_removal_lock(
        db: AsyncSession, tenant_id: UUID, organization_ids: Collection[UUID] | None
    ) -> TenantOrgWriteLock:
        result = await original(db, tenant_id, organization_ids)
        locked.set()
        await release.wait()
        return result

    monkeypatch.setattr(member_exit, "lock_tenant_org_write", hold_removal_lock)

    async def add_member() -> None:
        async with native_sessions() as db:
            organization = await db.get(Organization, organization_id)
            assert organization is not None
            context = OrgControlContext(organization, fixture.actor, ("can_manage_members",))
            mutation = OrgMutationRequest(db, audit_request(), etag(organization.updated_at))
            try:
                await create_member(mutation, context, MemberCreate(user_id=fixture.target_id))
            except AppError as error:
                add_error.append(error.code)
                await db.rollback()

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(remove_company, native_sessions, fixture)
                await locked.wait()
                tasks.start_soon(add_member)
                await anyio.sleep(0.1)
                release.set()
        assert add_error == ["not_found"]
        await assert_removed(native_sessions, fixture)
        async with native_sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action == "org.member_add",
                        AuditEvent.event_metadata["org_id"].as_string() == str(organization_id),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)


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


async def test_native_company_exit_reloads_cached_role_after_committed_revocation(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    try:
        async with native_sessions() as cached_db:
            cached = await cached_db.scalar(
                select(TenantRoleGrant).where(
                    TenantRoleGrant.tenant_id == fixture.tenant_id,
                    TenantRoleGrant.user_id == fixture.actor.id,
                )
            )
            assert cached is not None and cached.role == "admin"
            await cached_db.commit()
            async with native_sessions() as revoker:
                await revoker.execute(
                    update(TenantRoleGrant)
                    .where(TenantRoleGrant.id == cached.id)
                    .values(role="auditor")
                    .execution_options(synchronize_session=False)
                )
                await revoker.commit()

            with pytest.raises(AppError) as captured:
                await exit_tenant_member(
                    TenantMemberExitRequest(
                        cached_db, fixture.tenant_id, fixture.target_id, fixture.actor
                    )
                )
            assert (captured.value.status, captured.value.code) == (
                403,
                "permission_required",
            )
            assert cached.role == "auditor"
            await cached_db.rollback()

        async with native_sessions() as verification:
            member = await verification.get(TenantMember, (fixture.tenant_id, fixture.target_id))
            assert member is not None and member.status == "active"
            assert (
                await verification.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action.in_(("org.member_remove", "tenant.member_remove")),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_company_exit_rejects_suspended_company(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
        await db.execute(
            update(Tenant).where(Tenant.id == fixture.tenant_id).values(status="suspended")
        )
        await db.commit()
    try:
        async with native_sessions() as db:
            with pytest.raises(AppError) as captured:
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            assert (captured.value.status, captured.value.code) == (
                403,
                "tenant_suspended",
            )
            await db.rollback()
        async with native_sessions() as verification:
            member = await verification.get(TenantMember, (fixture.tenant_id, fixture.target_id))
            assert member is not None and member.status == "active"
            assert (
                await verification.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action.in_(("org.member_remove", "tenant.member_remove")),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_denied_company_exit_preserves_unrelated_pending_tenant(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    try:
        async with native_sessions() as db:
            pending = Tenant(
                name="Native pending company",
                slug=f"native-pending-{uuid4().hex}",
                secrets_namespace=f"native/pending/{uuid4().hex}",
            )
            db.add(pending)
            flush = Mock()
            event.listen(db.sync_session, "before_flush", flush)
            target_actor = CurrentUser(
                id=fixture.target_id,
                email=fixture.target_email,
                name="Native target",
                is_super_user=False,
            )
            try:
                with pytest.raises(AppError) as captured:
                    await exit_tenant_member(
                        TenantMemberExitRequest(
                            db, fixture.tenant_id, fixture.target_id, target_actor
                        )
                    )
            finally:
                event.remove(db.sync_session, "before_flush", flush)
            with db.no_autoflush:
                persisted = await db.scalar(select(Tenant.id).where(Tenant.slug == pending.slug))
            assert (captured.value.status, captured.value.code) == (
                403,
                "permission_required",
            )
            assert flush.call_count == 0
            assert pending in db.new
            assert persisted is None
            await db.rollback()
    finally:
        await cleanup_company(native_sessions, fixture)
