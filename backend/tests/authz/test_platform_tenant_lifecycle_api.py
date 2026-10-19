"""Operator suspend/resume: SUPER only, ETag/CSRF guarded, idempotent, audit atomic."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.refresh_token import RefreshToken
from app.services import audit_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.platform_tenant_lifecycle_app import LifecycleCase

pytest_plugins = ["tests.authz.platform_tenant_lifecycle_app"]
STALE = '"2001-01-01T00:00:00+00:00"'


async def _status(case: LifecycleCase) -> str:
    async with TestSession() as db:
        tenant = await db.get(Tenant, case.tenant_id)
        assert tenant is not None
        return tenant.status


async def _audits(case: LifecycleCase) -> Counter[str]:
    async with TestSession() as db:
        actions = await db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.target_id == str(case.tenant_id),
                AuditEvent.action.in_(("tenant.suspend", "tenant.resume")),
            )
        )
        return Counter(actions)


async def _authority_rows(case: LifecycleCase) -> tuple[int, ...]:
    async with TestSession() as db:
        counts: list[int] = []
        for model, column in (
            (TenantMember, TenantMember.user_id),
            (OrganizationMember, OrganizationMember.user_id),
            (TenantRoleGrant, TenantRoleGrant.user_id),
            (OrganizationRoleGrant, OrganizationRoleGrant.principal_id),
        ):
            query = select(func.count()).select_from(model).where(column == case.member_id)
            counts.append(int(await db.scalar(query) or 0))
        live_tokens = select(func.count()).select_from(RefreshToken)
        live_tokens = live_tokens.where(
            RefreshToken.user_id == case.member_id, RefreshToken.revoked_at.is_(None)
        )
        counts.append(int(await db.scalar(live_tokens) or 0))
        return tuple(counts)


async def test_suspend_resume_round_trip_is_versioned_idempotent_and_audited(
    lifecycle_case: LifecycleCase,
) -> None:
    case = lifecycle_case
    initial = await case.version()
    authority = await _authority_rows(case)
    assert authority[0] == 1 and authority[2] == 1 and authority[-1] >= 1

    suspended = await case.transition("suspend", version=initial)
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"
    assert suspended.headers["etag"] != initial
    assert suspended.headers["etag"] == await case.version()
    assert "secrets_namespace" not in suspended.text

    repeated = await case.transition("suspend", version=suspended.headers["etag"])
    assert repeated.status_code == 200 and repeated.json()["status"] == "suspended"
    assert repeated.headers["etag"] == suspended.headers["etag"]
    assert await _audits(case) == Counter({"tenant.suspend": 1})
    assert await _authority_rows(case) == authority

    resumed = await case.transition("resume", version=suspended.headers["etag"])
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"
    again = await case.transition("resume", version=resumed.headers["etag"])
    assert again.status_code == 200 and again.headers["etag"] == resumed.headers["etag"]
    assert await _audits(case) == Counter({"tenant.suspend": 1, "tenant.resume": 1})
    assert await _authority_rows(case) == authority

    async with TestSession() as db:
        event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.suspend"))
    assert event is not None and event.actor_user_id == case.operator_id
    assert event.outcome == "success"
    assert event.event_metadata == {
        "tenant_id": str(case.tenant_id),
        "previous_status": "active",
        "status": "suspended",
    }


@pytest.mark.parametrize("version", [None, STALE, "garbage"])
async def test_missing_or_stale_if_match_is_409_without_change(
    lifecycle_case: LifecycleCase, version: str | None
) -> None:
    case = lifecycle_case
    before = await case.version()
    headers = {"X-CSRF-Token": case.operator_csrf}
    if version is not None:
        headers["If-Match"] = version
    response = await case.operator.post(
        f"/api/admin/tenants/{case.tenant_id}/suspend", headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_version"
    assert (await _status(case), await case.version()) == ("active", before)
    assert await _audits(case) == Counter()


async def test_stale_resume_of_suspended_company_is_409(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    suspended = await case.transition("suspend")
    assert suspended.status_code == 200
    stale = await case.transition("resume", version=STALE)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    assert await _status(case) == "suspended"
    assert await _audits(case) == Counter({"tenant.suspend": 1})


async def test_csrf_is_required_before_any_change(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    response = await case.transition("suspend", csrf=False)
    assert response.status_code == 403
    assert await _status(case) == "active" and await _audits(case) == Counter()


async def test_company_member_and_admin_cannot_suspend(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    response = await case.member.post(
        f"/api/admin/tenants/{case.tenant_id}/suspend",
        headers={**case.member_headers(), "If-Match": await case.version()},
    )
    assert response.status_code == 403
    assert await _status(case) == "active" and await _audits(case) == Counter()


async def test_platform_routes_are_hidden_in_installed_mode(
    lifecycle_case: LifecycleCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "single_tenant")
    response = await lifecycle_case.transition("suspend")
    assert response.status_code == 404
    assert await _status(lifecycle_case) == "active"


async def test_unknown_company_is_404(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    response = await case.operator.post(
        "/api/admin/tenants/00000000-0000-4000-8000-00000000abcd/suspend",
        headers={"X-CSRF-Token": case.operator_csrf, "If-Match": STALE},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_optional_reason_is_recorded_sanitized(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    response = await case.transition("suspend", body={"reason": "  billing\n\x00hold  "})
    assert response.status_code == 200, response.text
    resumed = await case.transition("resume", body={"reason": "   "})
    assert resumed.status_code == 200
    async with TestSession() as db:
        rows = (
            await db.execute(
                select(AuditEvent.action, AuditEvent.reason_message).where(
                    AuditEvent.target_id == str(case.tenant_id)
                )
            )
        ).all()
    assert sorted(map(tuple, rows)) == [("tenant.resume", None), ("tenant.suspend", "billing hold")]


@pytest.mark.parametrize("body", [{"reason": "x" * 501}, {"reason": "ok", "extra": 1}])
async def test_invalid_reason_body_is_422_without_change(
    lifecycle_case: LifecycleCase, body: dict[str, object]
) -> None:
    case = lifecycle_case
    response = await case.transition("suspend", body=body)
    assert response.status_code == 422
    assert await _status(case) == "active" and await _audits(case) == Counter()


@pytest.mark.parametrize("action", ["suspend", "resume"])
async def test_deletion_scheduled_company_is_not_transitioned(
    lifecycle_case: LifecycleCase, action: str
) -> None:
    case = lifecycle_case
    async with TestSession() as db:
        tenant = await db.get(Tenant, case.tenant_id)
        assert tenant is not None
        tenant.status = "deletion_scheduled"
        await db.commit()
    response = await case.transition(action)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tenant_status_conflict"
    assert await _status(case) == "deletion_scheduled" and await _audits(case) == Counter()


async def test_audit_failure_rolls_back_the_status_change(
    lifecycle_case: LifecycleCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = lifecycle_case
    before = await case.version()

    async def failing_audit(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("audit write failed")

    monkeypatch.setattr(audit_service, "record_event", failing_audit)
    response = await case.transition("suspend", version=before)
    assert response.status_code == 500
    assert (await _status(case), await case.version()) == ("active", before)
    assert await _audits(case) == Counter()
