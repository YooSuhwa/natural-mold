"""I-07 permanent company purge: company rows 0 everywhere, the other company untouched.

Done-when (plan I-07): "삭제 후 회사 행 0, 다른 회사 영향 0". Tables are enumerated from
the ORM metadata, never from a list: every ``tenant_id`` table must hold zero rows of
the purged company, every other table loses exactly the purged company's rows, and every
row of the other company is still there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.authz import AuthzOutbox
from app.models.organization import Tenant
from app.models.refresh_token import RefreshToken
from app.models.tenant_export import TenantExport
from app.models.tenant_purge import TenantPurge
from app.models.user import User
from app.services.platform import tenant_purge
from app.services.platform.tenant_data_scope import company_tables, metadata
from app.services.platform.tenant_purge_job import run_tenant_purge_job
from tests.authz.i07_company_seed import SeededCompany, seed_company, share_member
from tests.conftest import TestSession

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
GROWS = {"audit_events", "authz_outbox", "tenant_purges"}  # tenant.deleted, tuples, receipt


async def _counts(where_tenant: UUID | None = None) -> dict[str, int]:
    async with TestSession() as db:
        if where_tenant is None:
            tables = metadata().sorted_tables
            return {
                t.name: int(await db.scalar(select(func.count()).select_from(t)) or 0)
                for t in tables
            }
        found: dict[str, int] = {}
        for item in company_tables(where_tenant):
            query = select(func.count()).select_from(item.table).where(item.where)
            found[item.table.name] = int(await db.scalar(query) or 0)
        return found


async def _schedule(company: SeededCompany, due: datetime) -> None:
    async with TestSession() as db:
        tenant = await db.get(Tenant, company.tenant_id)
        assert tenant is not None
        tenant.status, tenant.deletion_due_at = "deletion_scheduled", due
        await db.commit()


@pytest.fixture
async def companies(tmp_path: Path) -> tuple[SeededCompany, SeededCompany]:
    async with TestSession() as db:
        doomed = await seed_company(db, tmp_path, "doomed")
        other = await seed_company(db, tmp_path, "other")
        await share_member(db, doomed.owner_ids[1], other)
    return doomed, other


async def test_purge_deletes_every_company_row_and_leaves_the_other_company(
    companies: tuple[SeededCompany, SeededCompany], monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed, other = companies
    monkeypatch.setattr(settings, "authz_mode", "shadow")
    await _schedule(doomed, NOW - timedelta(minutes=1))
    doomed_rows, other_rows, totals = (
        await _counts(doomed.tenant_id),
        await _counts(other.tenant_id),
        await _counts(),
    )
    assert sum(1 for count in doomed_rows.values() if count) >= 30  # a broad graph

    results = await run_tenant_purge_job(maker=TestSession, now=NOW)

    assert [result.tenant_id for result in results] == [doomed.tenant_id]
    assert results[0].tuples_queued > 0 and results[0].sessions_revoked == 1
    after = await _counts()
    for table in metadata().sorted_tables:
        if "tenant_id" in table.c and table.name != "tenant_purges":  # the receipt stays
            async with TestSession() as db:
                query = select(func.count()).select_from(table)
                left = await db.scalar(query.where(table.c.tenant_id == doomed.tenant_id))
            assert left == 0, table.name
        expected = totals[table.name] - doomed_rows.get(table.name, 0)
        if table.name == "tenants":
            expected = totals[table.name] - 1
        if table.name in GROWS:
            assert after[table.name] >= expected, table.name
        else:
            assert after[table.name] == expected, table.name
    assert await _counts(other.tenant_id) == other_rows
    assert await _counts(doomed.tenant_id) == dict.fromkeys(doomed_rows, 0)


async def test_purge_leaves_only_tenant_deleted_and_cleans_files_and_sessions(
    companies: tuple[SeededCompany, SeededCompany],
) -> None:
    doomed, other = companies
    await _schedule(doomed, NOW - timedelta(days=1))
    await run_tenant_purge_job(maker=TestSession, now=NOW)
    async with TestSession() as db:
        events = (
            await db.execute(
                select(AuditEvent.action, AuditEvent.tenant_id).where(
                    AuditEvent.target_id == str(doomed.tenant_id)
                )
            )
        ).all()
        assert events == [("tenant.deleted", None)]
        only_member, shared = doomed.owner_ids
        live = select(RefreshToken.user_id).where(RefreshToken.revoked_at.is_(None))
        assert set(await db.scalars(live)) >= {shared, *other.owner_ids}
        assert only_member not in set(await db.scalars(live))
        assert await db.get(User, only_member) is not None  # accounts are global
        assert (await db.get(User, only_member)).last_active_org_id is None  # type: ignore[union-attr]
        assert int(await db.scalar(select(func.count()).select_from(AuthzOutbox)) or 0) == 0
    assert not doomed.skill_dir.exists() and not doomed.attachment_path.exists()
    assert not doomed.export_path.exists()
    assert other.skill_dir.exists() and other.attachment_path.exists()
    assert other.export_path.exists()


async def test_purge_waits_for_due_time_leadership_and_exports(
    companies: tuple[SeededCompany, SeededCompany],
) -> None:
    doomed, _ = companies
    before = await _counts()
    await _schedule(doomed, NOW + timedelta(hours=1))
    assert await run_tenant_purge_job(maker=TestSession, now=NOW) == []
    await _schedule(doomed, NOW - timedelta(hours=1))
    assert await run_tenant_purge_job(maker=TestSession, now=NOW, still_leader=_never) == []
    async with TestSession() as db:
        db.add(TenantExport(tenant_id=doomed.tenant_id, status="running", progress=3))
        await db.commit()
    assert await run_tenant_purge_job(maker=TestSession, now=NOW) == []  # export_in_progress
    after = await _counts()
    assert after["tenant_exports"] == before["tenant_exports"] + 1
    assert {k: v for k, v in after.items() if k != "tenant_exports"} == {
        k: v for k, v in before.items() if k != "tenant_exports"
    }


async def _never() -> bool:
    return False


async def test_cleanup_after_the_row_commit_resumes_and_is_idempotent(
    companies: tuple[SeededCompany, SeededCompany], monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed, other = companies
    await _schedule(doomed, NOW - timedelta(hours=1))
    broken = AsyncMock(side_effect=OSError("disk gone"))
    monkeypatch.setattr(tenant_purge, "remove_company_files", broken)
    assert await run_tenant_purge_job(maker=TestSession, now=NOW) == []  # cleanup failed
    async with TestSession() as db:
        assert await db.get(Tenant, doomed.tenant_id) is None  # rows committed
        receipt = await db.get(TenantPurge, doomed.tenant_id)
        assert receipt is not None and receipt.status == "rows_deleted"
    assert sum((await _counts(doomed.tenant_id)).values()) == 0
    assert doomed.skill_dir.exists()  # files wait for the cleanup
    monkeypatch.undo()
    assert await run_tenant_purge_job(maker=TestSession, now=NOW) == []
    async with TestSession() as db:
        receipt = await db.get(TenantPurge, doomed.tenant_id, populate_existing=True)
        assert receipt is not None and receipt.status == "completed"
    assert not doomed.skill_dir.exists() and other.skill_dir.exists()
    before = await _counts()
    assert await run_tenant_purge_job(maker=TestSession, now=NOW) == []
    assert await tenant_purge.purge_tenant(TestSession, doomed.tenant_id, now=NOW) is None
    assert await _counts() == before
