"""Late company invitation signup failures roll back every new fact."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

import pytest
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.organization import TenantMember, TenantRoleGrant
from app.models.user import User
from app.services import audit_service
from app.services.audit_service import AuditOutcome
from tests.authz.tenant_invitation_fixture import seed_tenant_invitation

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_late_audit_failure_rolls_back_user_membership_role_and_consumption(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="rollback-company@example.com",
    )

    async def fail_audit(
        db: AsyncSession,
        *,
        actor_type: str,
        action: str,
        target_type: str,
        outcome: AuditOutcome | str,
        actor_user_id: UUID | None = None,
        actor_api_key_id: UUID | None = None,
        actor_email_snapshot: str | None = None,
        actor_label: str | None = None,
        owner_user_id: UUID | None = None,
        owner_email_snapshot: str | None = None,
        target_id: str | UUID | None = None,
        target_name_snapshot: str | None = None,
        target_owner_user_id: UUID | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
        request: Request | None = None,
        request_id_override: str | None = None,
        trace_id: str | None = None,
        run_id: str | UUID | None = None,
        ip_address: str | None = None,
        user_agent_value: str | None = None,
        metadata: dict[str, str | bool | None] | None = None,
    ) -> NoReturn:
        del (
            db,
            actor_type,
            action,
            target_type,
            outcome,
            actor_user_id,
            actor_api_key_id,
            actor_email_snapshot,
            actor_label,
            owner_user_id,
            owner_email_snapshot,
            target_id,
            target_name_snapshot,
            target_owner_user_id,
            reason_code,
            reason_message,
            request,
            request_id_override,
            trace_id,
            run_id,
            ip_address,
            user_agent_value,
            metadata,
        )
        raise AppError(code="conflict", message="controlled late failure", status=409)

    monkeypatch.setattr(audit_service, "record_event", fail_audit)
    result = await raw_client.post(
        "/api/auth/register",
        json={
            "email": "rollback-company@example.com",
            "password": "correct horse",
            "name": "Rollback",
            "invitation_token": token,
        },
    )
    assert result.status_code == 409
    assert (
        await db.scalar(select(User.id).where(User.email == "rollback-company@example.com")) is None
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.tenant_id == org_fixture.tenant)
        )
        == 1
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(TenantRoleGrant.tenant_id == org_fixture.tenant)
        )
        == 0
    )
    await db.refresh(invitation)
    assert invitation.status == "pending" and invitation.accepted_at is None
