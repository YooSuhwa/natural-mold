"""Background jobs on real PostgreSQL with RLS on and a non-owner app role (순번 5).

Two workers claiming at once never take the same job (``FOR UPDATE SKIP LOCKED``), and a
job's company session sees only that company's jobs. Lane only (``-m integration``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import SessionTenant, platform_session, tenant_session
from app.jobs import service
from app.models.job import Job
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration
Maker = async_sessionmaker[database.ShieldedAsyncSession]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture(autouse=True)
def rls_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)


@pytest.fixture
async def app_maker(lab: RlsLab) -> AsyncIterator[Maker]:
    engine = create_async_engine(async_url(lab.app_url), pool_size=4)
    try:
        yield async_sessionmaker(engine, class_=database.ShieldedAsyncSession)
    finally:
        await engine.dispose()


def _org(lab: RlsLab, tenant: UUID) -> UUID:
    org = uuid4()
    with lab.admin.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO organizations (id, tenant_id, name, slug, status, is_default, "
                "settings, authz_epoch, created_at, updated_at) VALUES (:id, :tenant, 'Org', "
                ":slug, 'active', false, '{}', 0, now(), now())"
            ),
            {"id": org, "tenant": tenant, "slug": f"o-{org.hex[:10]}"},
        )
    return org


def _jobs(lab: RlsLab, tenant: UUID, org: UUID, count: int) -> list[UUID]:
    ids = [uuid4() for _ in range(count)]
    with lab.admin.begin() as connection:
        for job_id in ids:
            connection.execute(
                sa.text(
                    "INSERT INTO jobs (id, tenant_id, org_id, type, payload, state, progress, "
                    "attempts, max_attempts, cancel_requested, run_after, created_at, updated_at) "
                    "VALUES (:id, :tenant, :org, 'test.pg', '{}', 'queued', 0, 0, 3, false, "
                    "now() - interval '1 second', now(), now())"
                ),
                {"id": job_id, "tenant": tenant, "org": org},
            )
    return ids


async def test_two_workers_never_take_the_same_job(lab: RlsLab, app_maker: Maker) -> None:
    org = _org(lab, lab.tenant_a)
    created = set(_jobs(lab, lab.tenant_a, org, 6))

    async def take(worker: str) -> list[UUID]:
        taken: list[UUID] = []
        while True:
            async with platform_session("jobs.worker", maker=app_maker) as db:
                claim = await service.claim(db, worker_id=worker, types=("test.pg",), lease={})
            if claim is None:
                return taken
            taken.append(claim.job_id)

    first, second = await asyncio.gather(take("w1"), take("w2"))

    assert set(first) | set(second) == created
    assert not set(first) & set(second)


async def test_a_company_session_sees_only_its_own_jobs(lab: RlsLab, app_maker: Maker) -> None:
    org_a, org_b = _org(lab, lab.tenant_a), _org(lab, lab.tenant_b)
    [job_a] = _jobs(lab, lab.tenant_a, org_a, 1)
    [job_b] = _jobs(lab, lab.tenant_b, org_b, 1)

    async with platform_session("jobs.worker", maker=app_maker) as db:
        await db.execute(
            sa.update(Job).where(Job.id == job_b).values(state="running", locked_by="w1")
        )
        await db.commit()

    async with tenant_session(SessionTenant(lab.tenant_a, None), maker=app_maker) as db:
        assert await db.get(Job, job_a) is not None
        assert await db.get(Job, job_b) is None
        # Company A's session cannot finish company B's job even with the right claim.
        assert not await service.succeed(db, job_b, "w1", {"x": 1})
    async with tenant_session(SessionTenant(lab.tenant_b, None), maker=app_maker) as db:
        row = await db.get(Job, job_b)
        assert row is not None and row.state == "running"
