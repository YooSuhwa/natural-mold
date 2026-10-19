"""Each public catalog consumer refreshes captured authority before target SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember, Tenant
from app.models.tool import Tool
from app.models.user import User
from tests.authz.catalog_actor_fixture import (
    catalog_sql,
    consume_catalog,
    pending_tool,
)
from tests.authz.catalog_actor_fixture import (
    catalog_witness as catalog_witness,
)
from tests.authz.org_context_fixture import org_fixture as org_fixture

if TYPE_CHECKING:
    from tests.authz.catalog_actor_fixture import CatalogAction, CatalogWitness

type AuthorityState = Literal["active", "removed", "disabled", "suspended_company", "archived_org"]


@pytest.mark.parametrize(
    "case",
    [
        (action, state)
        for action in ("detail", "version", "list", "page", "versions", "project")
        for state in ("active", "removed", "disabled", "suspended_company", "archived_org")
    ],
)
async def test_private_catalog_consumer_rechecks_captured_actor(
    db: AsyncSession, catalog_witness: CatalogWitness, case: tuple[CatalogAction, AuthorityState]
) -> None:
    action, state = case
    context = catalog_witness.context
    version_id = catalog_witness.version.id
    match state:
        case "active":
            pass
        case "removed":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == context.org_id,
                    OrganizationMember.user_id == context.user_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
        case "disabled":
            await db.execute(
                update(User)
                .where(User.id == context.user_id)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
        case "suspended_company":
            await db.execute(
                update(Tenant)
                .where(Tenant.id == context.tenant_id)
                .values(status="suspended")
                .execution_options(synchronize_session=False)
            )
        case "archived_org":
            await db.execute(
                update(Organization)
                .where(Organization.id == context.org_id)
                .values(status="archived", archived_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    pending = pending_tool(catalog_witness)
    db.add(pending)
    consumed = False
    with catalog_sql(db) as sql:
        try:
            consumed = await consume_catalog(db, catalog_witness, action)
        except AppError as error:
            assert error.status in {403, 404}
    if state == "active":
        assert consumed
        await db.flush()
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id
    else:
        assert not consumed and sql == [] and pending in db.new
    await db.rollback()
    if state in {"active", "removed"}:
        catalog_witness.client.cookies.set(ACTIVE_ORG_COOKIE, str(context.org_id))
        response = await catalog_witness.client.get(f"/api/marketplace/versions/{version_id}")
        assert response.status_code == (200 if state == "active" else 403), response.text
