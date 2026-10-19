"""Migrated-PostgreSQL rollback guarantees for organization creation."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMember, OrganizationRoleGrant
from app.models.organization_group import OrgInvitation
from app.services.tenants import organizations as organization_service
from tests.authz.tenant_organizations_native_fixture import (
    NativeTask7Case,
    NativeTask7Sessions,
    native_task7_client,
    seed_native_task7,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("native_task7_cleanup_guard")]
pytest_plugins = ["tests.authz.tenant_organizations_native_fixture"]


async def _counts(sessions: NativeTask7Sessions, case: NativeTask7Case) -> tuple[int, ...]:
    async with sessions.observer() as db:
        values = (
            await db.scalar(
                select(func.count())
                .select_from(Organization)
                .where(Organization.tenant_id == case.tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.tenant_id == case.tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrganizationRoleGrant)
                .where(OrganizationRoleGrant.tenant_id == case.tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrgInvitation)
                .where(OrgInvitation.tenant_id == case.tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == case.actor_id)
            ),
        )
    assert all(value is not None for value in values)
    return tuple(int(value or 0) for value in values)


async def test_unique_constraint_loser_rolls_back_and_clean_retry_succeeds(
    native_task7_sessions: NativeTask7Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with native_task7_client(native_task7_sessions) as client:
        case = await seed_native_task7(native_task7_sessions, client)
        async with native_task7_sessions.observer() as db:
            occupied = await db.scalar(
                select(Organization.slug).where(Organization.id == case.org_id)
            )
        assert occupied is not None
        before = await _counts(native_task7_sessions, case)
        original = organization_service.organization_slug_for_ordinal
        candidates = iter(("available-to-check", occupied))

        def collide_after_allocation_check(base: str, ordinal: int) -> str:
            del base, ordinal
            return next(candidates)

        monkeypatch.setattr(
            organization_service,
            "organization_slug_for_ordinal",
            collide_after_allocation_check,
        )
        failed = await client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json={"name": "Forced collision", "administrator": {"user_id": str(case.target_id)}},
            headers=case.auth.headers(),
        )
        assert failed.status_code == 500
        assert await _counts(native_task7_sessions, case) == before

        monkeypatch.setattr(organization_service, "organization_slug_for_ordinal", original)
        retried = await client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json={"name": "Forced collision", "administrator": {"user_id": str(case.target_id)}},
            headers=case.auth.headers(),
        )
        assert retried.status_code == 201
        assert retried.json()["organization"]["slug"] == "forced-collision"
        after = await _counts(native_task7_sessions, case)
        assert tuple(after[index] - before[index] for index in range(5)) == (1, 1, 1, 0, 3)


@pytest.mark.parametrize(
    "administrator",
    [
        pytest.param("member", id="member-admin"),
        pytest.param("email", id="email-invitation"),
    ],
)
async def test_late_commit_failure_after_member_or_invitation_rolls_back_everything(
    native_task7_sessions: NativeTask7Sessions,
    monkeypatch: pytest.MonkeyPatch,
    administrator: str,
) -> None:
    async with native_task7_client(native_task7_sessions) as client:
        case = await seed_native_task7(native_task7_sessions, client)
        before = await _counts(native_task7_sessions, case)
        original_commit = AsyncSession.commit
        expected_name = f"Atomic {administrator}"
        commit_failure_reached = False

        async def fail_commit(db: AsyncSession) -> None:
            nonlocal commit_failure_reached
            await db.flush()
            flushed_organization = await db.scalar(
                select(Organization.id).where(
                    Organization.tenant_id == case.tenant_id,
                    Organization.name == expected_name,
                )
            )
            assert flushed_organization is not None
            commit_failure_reached = True
            raise SQLAlchemyError("forced late commit failure")

        monkeypatch.setattr(AsyncSession, "commit", fail_commit)
        selection = (
            {"user_id": str(case.target_id)}
            if administrator == "member"
            else {"email": "atomicity@example.com"}
        )
        failed = await client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json={"name": expected_name, "administrator": selection},
            headers=case.auth.headers(),
        )
        monkeypatch.setattr(AsyncSession, "commit", original_commit)
        assert failed.status_code == 500
        assert commit_failure_reached
        assert await _counts(native_task7_sessions, case) == before
