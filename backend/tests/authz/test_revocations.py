"""16.4 / 8.6: revocation markers, finalization and the organization decision generation."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.revocations import (
    DecisionStamp,
    bump_epoch,
    count_pending,
    current_epoch,
    decision_stamp,
    finalize_revocations,
    mark_fga_deleted,
    oldest_pending_at,
    record_revocation,
    stamp_is_current,
)
from app.models.authz import AuthzRevocation
from tests.authz.authz_service_testkit import AGENT, ORG, OTHER_ORG
from tests.authz.revocation_testkit import (
    SUBJECT,
    _fresh_pending_hint,  # noqa: F401 - autouse fixture
    revoke,
    seed_org,
)
from tests.conftest import TestSession

STAMP = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


async def test_marker_is_written_inside_the_callers_transaction(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    await record_revocation(
        db, org_id=ORG, tenant_id=tenant, scope="grant", subject=SUBJECT, object=AGENT
    )
    assert await count_pending(db) == 1  # flushed, not committed
    assert await current_epoch(db, ORG) == 1
    await db.rollback()
    # Then: a rolled-back source change leaves neither a marker nor a new generation.
    async with TestSession() as other:
        assert await count_pending(other) == 0
        assert await current_epoch(other, ORG) == 0


async def test_committed_marker_is_pending_and_bumps_only_its_org(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    await seed_org(db, OTHER_ORG)
    before = await decision_stamp(db, ORG)
    other_before = await decision_stamp(db, OTHER_ORG)
    await revoke(db, ORG, tenant, "membership", f"organization:{ORG}")
    await revoke(db, ORG, tenant, "grant", AGENT)
    async with TestSession() as other:
        assert await count_pending(other) == 2
        assert await current_epoch(other, ORG) == before.epoch + 2
        # A decision reused during a run is stale after any revocation in its org only.
        assert not await stamp_is_current(other, before)
        assert await stamp_is_current(other, other_before)
        assert await stamp_is_current(other, await decision_stamp(other, ORG))


async def test_epoch_bumps_once_per_transaction_per_org(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    for obj in (AGENT, f"agent:{uuid4()}"):
        await record_revocation(
            db, org_id=ORG, tenant_id=tenant, scope="grant", subject=SUBJECT, object=obj
        )
    await db.commit()
    assert await current_epoch(db, ORG) == 1
    await record_revocation(
        db, org_id=ORG, tenant_id=tenant, scope="grant", subject=SUBJECT, object=AGENT
    )
    await db.rollback()  # a rolled-back transaction forgets its bump ...
    await revoke(db, ORG, tenant, "grant", AGENT)  # ... so the next one bumps again
    assert await current_epoch(db, ORG) == 2
    assert await count_pending(db) == 3


async def test_oldest_pending_at_reports_stuck_markers(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    assert await oldest_pending_at(db) is None
    first = await revoke(db, ORG, tenant, "grant", AGENT)
    assert await oldest_pending_at(db) is not None
    await finalize_revocations(db, [first])
    await db.commit()
    assert await oldest_pending_at(db) is None


async def test_missing_org_is_never_a_current_generation(db: AsyncSession):
    with pytest.raises(LookupError):
        await bump_epoch(db, uuid4())
    with pytest.raises(LookupError):
        await current_epoch(db, uuid4())
    assert not await stamp_is_current(db, DecisionStamp(uuid4(), 0))


async def test_fga_delete_confirmation_alone_keeps_the_marker_pending(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    marker = await revoke(db, ORG, tenant, "grant", AGENT)
    assert await mark_fga_deleted(db, [marker], now=STAMP) == 1
    await db.commit()
    assert await count_pending(db) == 1
    row = await db.scalar(select(AuthzRevocation).where(AuthzRevocation.id == marker))
    assert row is not None and row.fga_deleted_at is not None and row.finalized_at is None


async def test_finalize_closes_markers_idempotently(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    first = await revoke(db, ORG, tenant, "grant", AGENT)
    second = await revoke(db, ORG, tenant, "ownership", AGENT)
    assert await finalize_revocations(db, [first, second], now=STAMP) == 2
    assert await finalize_revocations(db, [first, second], now=STAMP) == 0
    assert await finalize_revocations(db, []) == 0
    assert await mark_fga_deleted(db, []) == 0
    await db.commit()
    assert await count_pending(db) == 0
    rows = (await db.scalars(select(AuthzRevocation))).all()
    assert all(r.finalized_at is not None and r.fga_deleted_at is not None for r in rows)


async def test_finalize_db_failure_keeps_the_marker(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    marker = await revoke(db, ORG, tenant, "role", f"organization:{ORG}")
    await finalize_revocations(db, [marker], now=STAMP)
    await db.rollback()  # e.g. the grant ``revoked`` update failed in the same transaction
    assert await count_pending(db) == 1


@pytest.mark.parametrize(
    ("scope", "subject", "obj"),
    [("nope", SUBJECT, AGENT), ("grant", "", AGENT), ("grant", SUBJECT, "x" * 101)],
    ids=["scope", "empty-subject", "long-object"],
)
async def test_invalid_markers_are_rejected(db: AsyncSession, scope, subject, obj):
    tenant = await seed_org(db, ORG)
    with pytest.raises(ValueError, match="revocation"):
        await record_revocation(
            db, org_id=ORG, tenant_id=tenant, scope=scope, subject=subject, object=obj
        )
