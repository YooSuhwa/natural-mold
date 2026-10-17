"""H-02: 1-hour merge, 24-hour owner_needed merge, 90-day retention, managers (14.2)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import notifications as writer
from app.authz.notifications import Target, notify, resource_managers
from app.models.authz import ResourceGrant
from app.models.organization import Tenant
from tests.authz.d04_support import OTHER_USER_ID, add_tool
from tests.authz.e01_support import FOREIGN_USER_ID, THIRD_USER_ID, add_group, add_member, seed
from tests.authz.h02_support import backdate, push, rows
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def test_merge_window_is_fixed_and_never_reopens_a_read_row(db: AsyncSession) -> None:
    tool = await add_tool(db)
    target = Target("tool", tool.id, "t")
    first = await push(db, target=target, level="viewer")
    created = first.created_at
    first.read_at = datetime.now(UTC)
    await db.commit()
    again = await push(db, target=target, level="editor", actor=OTHER_USER_ID)
    [row] = await rows(TEST_USER_ID)
    assert again.id == first.id and row.read_at is not None  # stays read
    assert row.created_at.replace(tzinfo=UTC) == created.replace(tzinfo=UTC)  # window anchor
    assert (row.payload, row.actor_id) == ({"target_name": "t", "level": "editor"}, OTHER_USER_ID)
    await backdate(row.id, datetime.now(UTC) - timedelta(hours=2))
    await push(db, target=target)
    assert len(await rows(TEST_USER_ID)) == 2


async def test_company_rows_merge_only_within_their_company(db: AsyncSession) -> None:
    other = Tenant(name="Other co", slug=f"h02-{uuid4().hex[:6]}", secrets_namespace=uuid4().hex)
    db.add(other)
    await db.commit()
    for tenant in (TEST_TENANT_ID, other.id, TEST_TENANT_ID):
        await notify(
            db,
            kind="tenant.suspended",
            recipients=[TEST_USER_ID],
            tenant_id=tenant,
            org_id=None,
            target=None,
        )
    await db.commit()
    assert sorted(str(r.tenant_id) for r in await rows(TEST_USER_ID)) == sorted(
        [str(TEST_TENANT_ID), str(other.id)]
    )


async def test_owner_needed_merges_for_a_day(db: AsyncSession) -> None:
    target = Target("credential", TEST_USER_ID, "Slack")
    row = await push(db, kind="connection.owner_needed", target=target)
    await backdate(row.id, datetime.now(UTC) - timedelta(hours=5))
    await push(db, kind="connection.owner_needed", target=target)
    assert len(await rows(TEST_USER_ID)) == 1


async def test_old_rows_are_pruned_on_write_and_actor_is_never_notified(
    db: AsyncSession,
) -> None:
    writer._PRUNED.clear()  # noqa: SLF001 - start of a day for this recipient
    old = await push(db, kind="trigger.blocked")
    await backdate(old.id, datetime.now(UTC) - timedelta(days=91))
    await push(db, kind="grant.received", actor=OTHER_USER_ID)  # pruned at most once a day
    assert len(await rows(TEST_USER_ID)) == 2
    writer._PRUNED.clear()  # noqa: SLF001 - a day later
    await push(db, kind="delegation.broken")
    assert [row.kind for row in await rows(TEST_USER_ID)] == ["grant.received", "delegation.broken"]
    assert (
        await notify(
            db,
            kind="grant.received",
            recipients=[OTHER_USER_ID],
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            target=None,
            actor_id=OTHER_USER_ID,
        )
        == []
    )


async def test_managers_are_owner_plus_manager_grants_of_active_members(
    db: AsyncSession,
) -> None:
    await add_member(db, THIRD_USER_ID)
    tool = await add_tool(db, owner=OTHER_USER_ID)
    group = await add_group(db, members=(THIRD_USER_ID,))
    for principal_type, principal_id, relation in (
        ("user", TEST_USER_ID, "manager"),
        ("group", group, "manager"),
        ("user", FOREIGN_USER_ID, "manager"),  # not a member of this organization
    ):
        db.add(
            ResourceGrant(
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                resource_type="tool",
                resource_id=tool.id,
                relation=relation,
                principal_type=principal_type,
                principal_id=principal_id,
            )
        )
    await db.commit()
    found = await resource_managers(db, TEST_ORG_ID, "tool", tool.id, mode="enforce")
    assert found == {OTHER_USER_ID, TEST_USER_ID, THIRD_USER_ID}
    legacy = await resource_managers(db, TEST_ORG_ID, "tool", tool.id, mode="legacy")
    assert legacy == {OTHER_USER_ID}
