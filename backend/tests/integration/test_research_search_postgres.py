"""Verify search reservations on separate connections as a tenant-scoped PostgreSQL role."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from functools import partial
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session
from app.models.organization import Organization, OrganizationMember, TenantMember
from app.models.research_search_usage import ResearchSearchUsage
from app.models.user import User
from app.services import research_search_policy as search
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration
Maker = async_sessionmaker[ShieldedAsyncSession]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture
async def app_maker(lab: RlsLab, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Maker]:
    engine = create_async_engine(async_url(lab.app_url), pool_size=4)
    maker = async_sessionmaker(engine, class_=ShieldedAsyncSession)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(search, "tenant_session", partial(tenant_session, maker=maker))
    try:
        yield maker
    finally:
        await engine.dispose()


def _actor(lab: RlsLab, tenant_id: UUID) -> search.ResearchSearchActor:
    org, user = uuid4(), uuid4()
    with lab.admin.begin() as db:
        db.execute(insert(User).values(id=user, email=f"{user.hex}@research.test", name="User"))
        db.execute(insert(TenantMember).values(tenant_id=tenant_id, user_id=user))
        db.execute(
            insert(Organization).values(
                id=org,
                tenant_id=tenant_id,
                name="Org",
                slug=org.hex,
                settings={"research_search": {"providers": ["tavily"], "monthly_attempt_limit": 1}},
            )
        )
        db.execute(insert(OrganizationMember).values(tenant_id=tenant_id, org_id=org, user_id=user))
    return search.ResearchSearchActor(tenant_id=tenant_id, org_id=org, user_id=user)


async def test_concurrent_searches_reserve_only_one_attempt(lab: RlsLab, app_maker: Maker) -> None:
    actor = _actor(lab, lab.tenant_a)
    gate = asyncio.Barrier(4)

    async def reserve() -> bool:
        await gate.wait()
        try:
            await search.reserve_attempt(actor, "tavily")
        except search.SearchPolicyError as error:
            assert "budget" in str(error)
            return False
        return True

    replies = await asyncio.gather(*(reserve() for _ in range(4)))
    assert replies.count(True) == 1
    async with tenant_session(actor, maker=app_maker) as db:
        count = await db.scalar(
            select(ResearchSearchUsage.attempts).where(ResearchSearchUsage.org_id == actor.org_id)
        )
        assert count == 1


async def test_other_company_cannot_view_or_consume_allowance(
    lab: RlsLab, app_maker: Maker
) -> None:
    first, second = _actor(lab, lab.tenant_a), _actor(lab, lab.tenant_b)
    with pytest.raises(search.SearchPolicyError, match="authorized"):
        await search.reserve_attempt(replace(second, org_id=first.org_id), "tavily")
    with pytest.raises(search.SearchPolicyError, match="authorized"):
        await search.reserve_attempt(replace(first, user_id=second.user_id), "tavily")

    await search.reserve_attempt(first, "tavily")
    async with tenant_session(second, maker=app_maker) as db:
        foreign = select(ResearchSearchUsage).where(ResearchSearchUsage.org_id == first.org_id)
        assert await db.scalar(foreign) is None
    await search.reserve_attempt(second, "tavily")


async def test_pending_reservation_observes_committed_suspension(
    lab: RlsLab, app_maker: Maker
) -> None:
    actor = _actor(lab, lab.tenant_a)
    async with tenant_session(actor, maker=app_maker) as admin:
        await lock_tenant_org_write(admin, actor.tenant_id, (actor.org_id,))
        blocker_pid = await admin.scalar(text("SELECT pg_backend_pid()"))
        await admin.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == actor.org_id,
                OrganizationMember.user_id == actor.user_id,
            )
            .values(status="suspended")
        )

        async def try_reserving() -> None:
            with pytest.raises(search.SearchPolicyError, match="authorized"):
                await search.reserve_attempt(actor, "tavily")

        async with asyncio.TaskGroup() as group:
            group.create_task(try_reserving())
            async with asyncio.timeout(5):
                while True:
                    async with app_maker() as observer:
                        waiting = await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT FROM pg_stat_activity "
                                "WHERE datname=current_database() AND wait_event_type='Lock' "
                                "AND :blocker=ANY(pg_blocking_pids(pid)))"
                            ),
                            {"blocker": blocker_pid},
                        )
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
            await admin.commit()
    async with tenant_session(actor, maker=app_maker) as db:
        count = select(ResearchSearchUsage).where(ResearchSearchUsage.org_id == actor.org_id)
        assert await db.scalar(count) is None
