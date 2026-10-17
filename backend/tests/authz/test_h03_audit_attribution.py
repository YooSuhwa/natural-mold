"""H-03 / 14.1: ``record_event`` writes ``audit_events.org_id`` (m89 column from F-14)."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.database import SessionTenant, bind_tenant, tenant_session
from app.services import audit_service
from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.e01_support import add_agent
from tests.authz.h03_support import FAR_ORG_ID, TEST_ORG_ID, TEST_TENANT_ID, seed_world
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio
CONTEXT = OrgContext(tenant_id=TEST_TENANT_ID, org_id=TEST_ORG_ID, user_id=TEST_USER_ID)


@pytest.fixture(autouse=True)
async def _world(db: AsyncSession) -> None:
    await seed_world(db)


async def _record(db: AsyncSession, **kwargs: object):
    base: dict[str, object] = {
        "actor_type": "user",
        "action": "x",
        "target_type": "user",
        "outcome": "success",
    }
    row = await audit_service.record_event(db, **{**base, **kwargs})  # type: ignore[arg-type]
    await db.commit()
    return row


async def test_session_bound_with_an_org_context_fills_org() -> None:
    async with tenant_session(CONTEXT, maker=TestSession) as db:
        row = await _record(db)
    assert (row.tenant_id, row.org_id) == (TEST_TENANT_ID, TEST_ORG_ID)


async def test_bind_tenant_with_org_context_fills_org() -> None:
    async with TestSession() as db:
        await bind_tenant(db, CONTEXT)
        row = await _record(db)
    assert row.org_id == TEST_ORG_ID


async def test_company_only_scope_leaves_org_empty() -> None:
    async with tenant_session(SessionTenant(TEST_TENANT_ID), maker=TestSession) as db:
        row = await _record(db)
    assert (row.tenant_id, row.org_id) == (TEST_TENANT_ID, None)


async def test_event_about_an_organization_wins_over_the_session_org() -> None:
    async with tenant_session(CONTEXT, maker=TestSession) as db:
        target = await _record(db, target_type="organization", target_id=OTHER_ORG_ID)
        named = await _record(db, metadata={"org_id": str(OTHER_ORG_ID)})
    assert target.org_id == OTHER_ORG_ID and named.org_id == OTHER_ORG_ID


async def test_organization_of_another_company_is_never_paired() -> None:
    async with tenant_session(CONTEXT, maker=TestSession) as db:
        far = await _record(db, metadata={"org_id": str(FAR_ORG_ID)})
        unknown = await _record(db, target_type="organization", target_id=uuid4())
    # Falls back to the session's own organization, never the foreign pair.
    assert (far.tenant_id, far.org_id) == (TEST_TENANT_ID, TEST_ORG_ID)
    assert unknown.org_id == TEST_ORG_ID


async def test_unbound_session_takes_company_from_the_named_org() -> None:
    async with TestSession() as db:
        row = await _record(db, target_type="organization", target_id=TEST_ORG_ID)
        plain = await _record(db)
    assert (row.tenant_id, row.org_id) == (TEST_TENANT_ID, TEST_ORG_ID)
    assert (plain.tenant_id, plain.org_id) == (None, None)


async def test_explicit_org_is_kept() -> None:
    async with TestSession() as db:
        row = await _record(db, tenant_id=TEST_TENANT_ID, org_id=OTHER_ORG_ID)
    assert row.org_id == OTHER_ORG_ID


async def test_action_on_another_orgs_resource_is_attributed_to_that_org() -> None:
    """Active org A, action on an org B agent -> org B (never the session org)."""
    async with TestSession() as seed_db:
        agent = await add_agent(seed_db)
        agent.org_id = OTHER_ORG_ID
        await seed_db.commit()
    async with tenant_session(CONTEXT, maker=TestSession) as db:
        row = await _record(db, target_type="agent", target_id=agent.id)
        gone = await _record(db, target_type="agent", target_id=uuid4())
    assert (row.tenant_id, row.org_id) == (TEST_TENANT_ID, OTHER_ORG_ID)
    assert gone.org_id is None  # resource target without a row: no session fallback
