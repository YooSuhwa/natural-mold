"""Plan 8.8 tests (I-06) on real PostgreSQL with m92 applied and a non-owner app role.

(1) no company context reads 0 rows, (2) another company's context reads 0 rows,
(3) the policy still applies in the second transaction after a commit, (4) derived
work records under the company it was started with. Lane only (``-m integration``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import SessionTenant, platform_session, tenant_session
from app.migrations.org_authz_v1.rls_activation import set_rls
from app.migrations.org_authz_v1.rls_manifest import RUNTIME_EXEMPT
from app.models.organization_identity import TenantDomain
from app.security.rls_role_check import read_role_facts, rls_role_issues
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration
Maker = async_sessionmaker[database.ShieldedAsyncSession]
COUNT = sa.select(sa.func.count()).select_from(TenantDomain)


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture(autouse=True)
def rls_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)


@pytest.fixture
async def app_maker(lab: RlsLab) -> AsyncIterator[Maker]:
    # One pooled connection: values must not survive into the next session.
    engine = create_async_engine(async_url(lab.app_url), pool_size=1, max_overflow=0)
    try:
        yield async_sessionmaker(engine, class_=database.ShieldedAsyncSession)
    finally:
        await engine.dispose()


def _company(tenant: UUID) -> SessionTenant:
    return SessionTenant(tenant, None)


async def test_1_no_company_context_reads_zero_rows(lab: RlsLab, app_maker: Maker) -> None:
    async with tenant_session(None, maker=app_maker) as db:
        assert (await db.execute(COUNT)).scalar_one() == 0
    async with platform_session("seed.startup", maker=app_maker) as db:
        assert (await db.execute(COUNT)).scalar_one() == 2


async def test_2_other_company_context_reads_zero_rows(lab: RlsLab, app_maker: Maker) -> None:
    async with tenant_session(_company(lab.tenant_a), maker=app_maker) as db:
        rows = (await db.execute(sa.select(TenantDomain.tenant_id))).scalars().all()
        assert rows == [lab.tenant_a]
        foreign = sa.select(TenantDomain).where(TenantDomain.tenant_id == lab.tenant_b)
        assert (await db.execute(foreign)).first() is None
        update = (
            sa.update(TenantDomain)
            .where(TenantDomain.tenant_id == lab.tenant_b)
            .values(status="verified")
            .returning(TenantDomain.id)
        )
        assert (await db.execute(update)).first() is None


async def test_3_policy_applies_after_commit_and_rollback(lab: RlsLab, app_maker: Maker) -> None:
    async with tenant_session(_company(lab.tenant_b), maker=app_maker) as db:
        for _ in range(2):
            assert (await db.execute(COUNT)).scalar_one() == 1
            await db.commit()
        await db.rollback()
        assert (await db.execute(COUNT)).scalar_one() == 1
        async with db.begin_nested():
            assert (await db.execute(COUNT)).scalar_one() == 1


async def _derived_record(ctx: SessionTenant, maker: Maker, tenant_id: UUID) -> UUID:
    """Derived work: receives the starting context explicitly, never via contextvars."""
    row_id = uuid4()  # kept outside the row: commit expires it and the session detaches it
    row = TenantDomain(
        id=row_id, tenant_id=tenant_id, domain=f"{uuid4().hex[:12]}.d.test", txt_token="t"
    )
    async with tenant_session(ctx, maker=maker) as db:
        db.add(row)
        await db.commit()
    return row_id


async def test_4_derived_work_records_under_the_original_company(
    lab: RlsLab, app_maker: Maker
) -> None:
    ctx = _company(lab.tenant_a)
    async with tenant_session(ctx, maker=app_maker) as request_db:
        assert (await request_db.execute(COUNT)).scalar_one() >= 1
    row_id = await asyncio.create_task(_derived_record(ctx, app_maker, lab.tenant_a))
    with lab.admin.connect() as connection:
        stored = connection.scalar(
            sa.text("SELECT tenant_id FROM tenant_domains WHERE id = :id"), {"id": row_id}
        )
    assert stored == lab.tenant_a
    async with tenant_session(_company(lab.tenant_b), maker=app_maker) as db:
        assert await db.get(TenantDomain, row_id) is None
    # WITH CHECK: work started for company A cannot write a company B row.
    with pytest.raises(DBAPIError, match="row-level security"):
        await _derived_record(ctx, app_maker, lab.tenant_b)


def test_every_protected_table_has_one_forced_policy(lab: RlsLab) -> None:
    with lab.admin.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity, count(p.oid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' GROUP BY c.oid, c.relname"
            )
        ).all()
    unprotected = {name for name, forced, policies in rows if not (forced and policies == 1)}
    assert unprotected == {name for name, _forced, _count in rows} & RUNTIME_EXEMPT


async def test_app_role_passes_the_startup_check_and_owner_does_not(
    lab: RlsLab, app_maker: Maker
) -> None:
    async with platform_session("startup.rls_role_check", maker=app_maker) as db:
        assert rls_role_issues(await read_role_facts(db)) == []
    owner = create_async_engine(async_url(lab.admin_url))
    try:
        maker = async_sessionmaker(owner, class_=database.ShieldedAsyncSession)
        async with platform_session("startup.rls_role_check", maker=maker) as db:
            issues = rls_role_issues(await read_role_facts(db))
    finally:
        await owner.dispose()
    assert any("owns (directly or by membership) Moldy tables" in issue for issue in issues)


async def test_disabling_rls_reopens_rows_and_enabling_closes_them(
    lab: RlsLab, app_maker: Maker
) -> None:
    with lab.admin.begin() as connection:
        set_rls(connection, enabled=False)
    try:
        async with tenant_session(None, maker=app_maker) as db:
            assert (await db.execute(COUNT)).scalar_one() >= 2
    finally:
        with lab.admin.begin() as connection:
            set_rls(connection, enabled=True)
    async with tenant_session(None, maker=app_maker) as db:
        assert (await db.execute(COUNT)).scalar_one() == 0
