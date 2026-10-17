"""I-07 operator routes: export, export list/download, deletion schedule and cancel.

SUPER + SaaS only, CSRF and If-Match like suspend/resume; a scheduled company is blocked
for its members; secrets never reach the archive.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.credential import Credential
from app.models.organization import Tenant
from app.models.tenant_export import TenantExport
from app.services.platform import tenant_exports
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.platform_tenant_lifecycle_app import LifecycleCase

pytest_plugins = ["tests.authz.platform_tenant_lifecycle_app"]
ROUTES = ("export", "schedule-deletion", "cancel-deletion")


@pytest.fixture(autouse=True)
def _export_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(tenant_exports, "async_session", TestSession)


async def _tenant(case: LifecycleCase) -> Tenant:
    async with TestSession() as db:
        tenant = await db.get(Tenant, case.tenant_id)
        assert tenant is not None
        return tenant


async def _audits(case: LifecycleCase) -> Counter[str]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AuditEvent.action).where(AuditEvent.target_id == str(case.tenant_id))
        )
        return Counter(rows)


async def test_schedule_and_cancel_deletion_block_members_and_audit(
    lifecycle_case: LifecycleCase,
) -> None:
    case = lifecycle_case
    tenant = await _tenant(case)
    wrong = await case.transition("schedule-deletion", body={"confirm_slug": "nope"})
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "tenant_deletion_slug_mismatch"

    scheduled = await case.transition("schedule-deletion", body={"confirm_slug": tenant.slug})
    assert scheduled.status_code == 200, scheduled.text
    body = scheduled.json()
    assert body["tenant"]["status"] == "deletion_scheduled"
    due = datetime.fromisoformat(body["deletion_due_at"]).astimezone(UTC)
    assert timedelta(days=29) < due - datetime.now(UTC) <= timedelta(days=30)
    assert scheduled.headers["etag"] == await case.version()
    blocked = await case.member.get(f"/api/tenants/{case.tenant_id}")
    assert blocked.status_code == 403

    again = await case.transition(
        "schedule-deletion",
        version=scheduled.headers["etag"],
        body={"confirm_slug": tenant.slug},
    )
    assert again.status_code == 200 and again.headers["etag"] == scheduled.headers["etag"]

    cancelled = await case.transition("cancel-deletion", version=scheduled.headers["etag"])
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["tenant"]["status"] == "suspended"
    assert cancelled.json()["deletion_due_at"] is None
    assert await _audits(case) == Counter(
        {"tenant.deletion_schedule": 1, "tenant.deletion_cancel": 1}
    )


async def test_cancel_after_the_due_time_works_until_the_purge_ran(
    lifecycle_case: LifecycleCase,
) -> None:
    case = lifecycle_case
    async with TestSession() as db:
        tenant = await db.get(Tenant, case.tenant_id)
        assert tenant is not None
        tenant.status = "deletion_scheduled"
        tenant.deletion_due_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    late = await case.transition("cancel-deletion")
    assert late.status_code == 200, late.text
    assert (await _tenant(case)).status == "suspended"


async def test_export_runs_lists_and_downloads_without_secrets(
    lifecycle_case: LifecycleCase,
) -> None:
    case = lifecycle_case
    async with TestSession() as db:
        db.add(
            Credential(
                tenant_id=case.tenant_id,
                org_id=case.org_id,
                user_id=case.member_id,
                name="Exported key",
                definition_key="http_bearer",
                data_encrypted="ciphertext-must-not-leak",
                key_id="fixture",
                field_keys=["token"],
            )
        )
        await db.commit()
    headers = {"X-CSRF-Token": case.operator_csrf}
    started = await case.operator.post(
        f"/api/admin/tenants/{case.tenant_id}/export", headers=headers
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    listed = await case.operator.get(f"/api/admin/tenants/{case.tenant_id}/exports")
    assert listed.status_code == 200
    [job] = listed.json()["items"]
    assert (job["id"], job["status"], job["progress"]) == (job_id, "succeeded", 100)
    assert job["download_available"] is True

    url = f"/api/admin/tenants/{case.tenant_id}/exports/{job_id}/download"
    download = await case.operator.get(url)
    assert download.status_code == 200
    assert b"ciphertext-must-not-leak" not in download.content
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["tenant_id"] == str(case.tenant_id)
    assert manifest["row_counts"]["credentials"] == 1
    assert manifest["redacted_columns"]["credentials"] == ["data_encrypted"]
    [row] = [json.loads(line) for line in archive.read("tables/credentials.jsonl").splitlines()]
    assert row["data_encrypted"] == "<redacted>" and row["name"] == "Exported key"
    users = archive.read("tables/users.jsonl").decode()
    assert str(case.member_id) in users and "hashed_password" not in users
    assert "last_login_ip" not in users
    assert await _audits(case) == Counter({"tenant.export": 1})
    member = await case.member.get(url)
    assert member.status_code == 403


async def test_one_active_export_and_unknown_download_is_404(
    lifecycle_case: LifecycleCase,
) -> None:
    case = lifecycle_case
    async with TestSession() as db:
        db.add(TenantExport(tenant_id=case.tenant_id, status="running", progress=5))
        await db.commit()
    headers = {"X-CSRF-Token": case.operator_csrf}
    busy = await case.operator.post(f"/api/admin/tenants/{case.tenant_id}/export", headers=headers)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "tenant_export_in_progress"
    async with TestSession() as db:
        running = await db.scalar(select(TenantExport.id))
    missing = await case.operator.get(
        f"/api/admin/tenants/{case.tenant_id}/exports/{running}/download"
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("route", ROUTES)
async def test_routes_need_csrf_and_an_operator(lifecycle_case: LifecycleCase, route: str) -> None:
    case = lifecycle_case
    body: dict[str, object] = {"confirm_slug": (await _tenant(case)).slug}
    no_csrf = await case.transition(route, csrf=False, body=body)
    assert no_csrf.status_code == 403
    member = await case.member.post(
        f"/api/admin/tenants/{case.tenant_id}/{route}",
        headers={**case.member_headers(), "If-Match": await case.version()},
        json=body,
    )
    assert member.status_code == 403
    assert (await _tenant(case)).status == "active"
    assert await _audits(case) == Counter()


async def test_single_tenant_deployment_hides_the_routes(
    lifecycle_case: LifecycleCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = lifecycle_case
    monkeypatch.setattr(settings, "deployment_mode", "single_tenant")
    response = await case.operator.get(f"/api/admin/tenants/{case.tenant_id}/exports")
    assert response.status_code == 404
