"""U-01 operator screens (S03/S04): company limits summary and deletion state reads.

``GET /api/admin/tenants/{id}/limits`` and ``/deletion`` are SUPER + SaaS only; limits show
amounts and counts only, the deletion state keeps the D-30 due date across reloads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.config import settings
from app.models.organization import Tenant
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.platform_tenant_lifecycle_app import LifecycleCase

pytest_plugins = ["tests.authz.platform_tenant_lifecycle_app"]


async def test_operator_reads_company_limits_summary(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    async with TestSession() as db:
        tenant = await db.get(Tenant, case.tenant_id)
        assert tenant is not None
        tenant.limits = {**tenant.limits, "member_limit": 500, "monthly_spend_limit": 100}
        await db.commit()

    response = await case.operator.get(f"/api/admin/tenants/{case.tenant_id}/limits")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "tenant" and body["id"] == str(case.tenant_id)
    assert body["member_limit"] == 500
    assert float(body["monthly_spend_limit"]) == 100
    assert float(body["used"]) == 0
    assert body["members"] >= 1 and body["active_runs"] == 0


async def test_limits_summary_is_operator_only_and_hides_unknown_companies(
    lifecycle_case: LifecycleCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = lifecycle_case
    path = f"/api/admin/tenants/{case.tenant_id}/limits"
    assert (await case.member.get(path)).status_code == 403
    missing = await case.operator.get(f"/api/admin/tenants/{uuid4()}/limits")
    assert missing.status_code == 404
    monkeypatch.setattr(settings, "deployment_mode", "single_tenant")
    assert (await case.operator.get(path)).status_code == 404


async def test_deletion_state_read_keeps_the_due_date(lifecycle_case: LifecycleCase) -> None:
    case = lifecycle_case
    path = f"/api/admin/tenants/{case.tenant_id}/deletion"
    before = await case.operator.get(path)
    assert before.status_code == 200, before.text
    assert before.json()["deletion_due_at"] is None
    assert before.json()["tenant"]["status"] == "active"
    async with TestSession() as db:
        tenant = await db.get(Tenant, case.tenant_id)
        assert tenant is not None
        slug = tenant.slug
    scheduled = await case.transition("schedule-deletion", body={"confirm_slug": slug})
    assert scheduled.status_code == 200, scheduled.text
    after = await case.operator.get(path)
    assert after.json() == scheduled.json()
    assert after.headers["etag"] == scheduled.headers["etag"] == await case.version()
    assert (await case.member.get(path)).status_code == 403
    assert (await case.operator.get(f"/api/admin/tenants/{uuid4()}/deletion")).status_code == 404
