"""An ORM identity-map cache is not an authority receipt for organization status."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import resolve_org_context
from app.exceptions import AppError
from app.models.organization import Tenant

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_context_reloads_company_status_when_identity_map_is_stale(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    # Given: the session has cached a company whose database status has changed.
    cached = await db.get(Tenant, org_fixture.tenant)
    assert cached is not None and cached.status == "active"
    await db.execute(
        update(Tenant)
        .where(Tenant.id == cached.id)
        .values(status="suspended")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert cached.status == "active"
    # When: authority is resolved again in that same session.
    with pytest.raises(AppError) as captured:
        await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    # Then: database suspension wins over the cached active model.
    assert captured.value.code == "tenant_suspended"
