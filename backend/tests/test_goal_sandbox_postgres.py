"""Goal sandbox writers, cleanup claims, and tenant isolation on PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import SessionTenant, tenant_session
from app.models import Organization, Tenant
from app.services import goal_sandbox_leases as leases
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration
Maker = async_sessionmaker[database.ShieldedAsyncSession]
TTL = timedelta(minutes=2)


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture
async def app_maker(lab: RlsLab, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Maker]:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    engine = create_async_engine(async_url(lab.app_url))
    try:
        yield async_sessionmaker(
            engine, class_=database.ShieldedAsyncSession, expire_on_commit=False
        )
    finally:
        await engine.dispose()


def _scope(lab: RlsLab) -> leases.SandboxScope:
    tenant, org = uuid4(), uuid4()
    with lab.admin.begin() as db:
        db.execute(
            sa.insert(Tenant).values(
                id=tenant, name="Tenant", slug=tenant.hex, secrets_namespace=tenant.hex
            )
        )
        db.execute(
            sa.insert(Organization).values(id=org, tenant_id=tenant, name="Org", slug=org.hex)
        )
    return leases.SandboxScope(
        tenant_id=tenant,
        org_id=org,
        user_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        goal_id=uuid4(),
        goal_version=1,
        policy_version=1,
        policy_hash="1" * 64,
        image="public-test-image",
        skill_id=uuid4(),
        skill_hash="2" * 64,
    )


async def test_two_workers_get_one_creation_claim(lab: RlsLab, app_maker: Maker) -> None:
    scope = _scope(lab)
    ready = asyncio.Barrier(2)
    tenant = SessionTenant(scope.tenant_id, scope.user_id)

    async def claim_one() -> leases.SandboxClaim | None:
        async with tenant_session(tenant, maker=app_maker) as db, db.begin():
            # Establish separate DB connections before the two inserts contend.
            await db.execute(sa.text("SELECT 1"))
            await ready.wait()
            return await leases.acquire(db, scope, ttl=TTL)

    async with asyncio.timeout(30):
        first, second = await asyncio.gather(claim_one(), claim_one())
    winners = [claim for claim in (first, second) if claim is not None]
    assert len(winners) == 1
    assert winners[0].action == "create"


async def _claim_pause(
    db: database.ShieldedAsyncSession, scope: leases.SandboxScope
) -> leases.SandboxClaim | None:
    now = datetime.now(UTC)
    return await leases.claim_maintenance(
        db, scope.tenant_id, idle_before=now, expire_before=now - timedelta(days=1), ttl=TTL
    )


async def _ready_workspace(app_maker: Maker, scope: leases.SandboxScope) -> leases.SandboxClaim:
    tenant = SessionTenant(scope.tenant_id, scope.user_id)
    async with tenant_session(tenant, maker=app_maker) as db, db.begin():
        claim = await leases.acquire(db, scope, ttl=TTL)
        assert claim is not None
        assert await leases.bind_provider(db, claim, str(uuid4()))
        assert await leases.record_outputs_ready(db, claim, manifest={})
        assert await leases.finish(db, claim, manifest={})
    return claim


async def test_ready_workspace_can_be_claimed_for_execution_or_pause_once(
    lab: RlsLab, app_maker: Maker
) -> None:
    scope = _scope(lab)
    tenant = SessionTenant(scope.tenant_id, scope.user_id)
    initial_claim = await _ready_workspace(app_maker, scope)
    ready = asyncio.Barrier(2)

    async def compete(*, pause: bool) -> leases.SandboxClaim | None:
        async with tenant_session(tenant, maker=app_maker) as db, db.begin():
            await db.execute(sa.text("SELECT 1"))
            await ready.wait()
            if pause:
                return await _claim_pause(db, scope)
            return await leases.acquire(db, scope, ttl=TTL)

    async with asyncio.timeout(30):
        results = await asyncio.gather(compete(pause=False), compete(pause=True))
    winners = [claim for claim in results if claim is not None]
    assert len(winners) == 1
    assert winners[0].workspace_id == initial_claim.workspace_id
    assert winners[0].action in {"connect", "pause"}


async def test_cleanup_cannot_win_while_a_reader_is_active(lab: RlsLab, app_maker: Maker) -> None:
    scope = _scope(lab)
    tenant = SessionTenant(scope.tenant_id, scope.user_id)
    claim = await _ready_workspace(app_maker, scope)
    async with tenant_session(tenant, maker=app_maker) as db, db.begin():
        token = await leases.acquire_read(db, scope, ttl=TTL)
        assert token is not None

    async def cleanup() -> leases.SandboxClaim | None:
        async with tenant_session(tenant, maker=app_maker) as db, db.begin():
            return await _claim_pause(db, scope)

    assert await cleanup() is None
    async with tenant_session(tenant, maker=app_maker) as db, db.begin():
        assert await leases.release_read(db, scope, token)
    async with asyncio.timeout(30):
        attempted = await asyncio.gather(cleanup(), cleanup())
    winners = [claim for claim in attempted if claim is not None]
    assert len(winners) == 1
    assert winners[0].action == "pause"
    assert winners[0].workspace_id == claim.workspace_id


async def test_other_company_cannot_see_or_modify_a_known_claim(
    lab: RlsLab, app_maker: Maker
) -> None:
    scope = _scope(lab)
    tenant = SessionTenant(scope.tenant_id, scope.user_id)
    async with tenant_session(tenant, maker=app_maker) as db, db.begin():
        claim = await leases.acquire(db, scope, ttl=TTL)
        assert claim is not None
    async with tenant_session(SessionTenant(lab.tenant_b), maker=app_maker) as db, db.begin():
        # Even a leaked valid operation token gives the other app session no rows.
        assert not await leases.bind_provider(db, claim, "foreign-id")
        assert (
            await db.scalar(
                sa.text("SELECT id FROM goal_sandboxes WHERE id=:wid"), {"wid": claim.workspace_id}
            )
            is None
        )
    async with tenant_session(tenant, maker=app_maker) as db, db.begin():
        assert await leases.bind_provider(db, claim, "own-id")
