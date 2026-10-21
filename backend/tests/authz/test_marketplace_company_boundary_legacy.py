"""Legacy marketplace reach ends at the company boundary (personal-tenant plan P1-6).

Enforced mode already limits company-public items to the publishing company; the legacy
candidate predicate showed published public/unlisted items to every company, which puts
strangers in separate personal companies in front of each other's items.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser, get_current_user, get_org_context
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.d05_support import BASE, add_item
from tests.authz.e09_support import OUTSIDER, acting_as, seed_outsider

FOREIGN_TENANT_ID = uuid5(NAMESPACE_URL, "moldy/p16-foreign-company")
FOREIGN_ORG_ID = uuid5(NAMESPACE_URL, "moldy/p16-foreign-organization")
FOREIGN = CurrentUser(
    id=uuid5(NAMESPACE_URL, "moldy/p16-foreign-user"),
    email="foreign@p16.example.com",
    name="Foreign",
    is_super_user=False,
)


async def _seed_foreign(db: AsyncSession) -> None:
    if await db.get(Tenant, FOREIGN_TENANT_ID) is None:
        db.add(
            Tenant(
                id=FOREIGN_TENANT_ID,
                name="Foreign",
                slug="p16-foreign",
                secrets_namespace=f"tenants/{FOREIGN_TENANT_ID}",
                settings={"kind": "personal"},
            )
        )
        await db.flush()
        db.add(
            Organization(
                id=FOREIGN_ORG_ID,
                tenant_id=FOREIGN_TENANT_ID,
                name="Foreign",
                slug="default",
                is_default=True,
            )
        )
        db.add(User(id=FOREIGN.id, email=FOREIGN.email, name=FOREIGN.name, is_active=True))
        await db.flush()
        db.add(TenantMember(tenant_id=FOREIGN_TENANT_ID, user_id=FOREIGN.id))
        db.add(
            OrganizationMember(
                org_id=FOREIGN_ORG_ID, tenant_id=FOREIGN_TENANT_ID, user_id=FOREIGN.id
            )
        )
    await db.commit()


@contextmanager
def _as_foreigner(app: FastAPI) -> Iterator[None]:
    saved = {dep: app.dependency_overrides.get(dep) for dep in (get_current_user, get_org_context)}

    async def current() -> CurrentUser:
        return FOREIGN

    async def context() -> OrgContext:
        return OrgContext(tenant_id=FOREIGN_TENANT_ID, org_id=FOREIGN_ORG_ID, user_id=FOREIGN.id)

    app.dependency_overrides[get_current_user] = current
    app.dependency_overrides[get_org_context] = context
    try:
        yield
    finally:
        for dep, previous in saved.items():
            if previous is not None:
                app.dependency_overrides[dep] = previous


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)
    await seed_outsider(db)
    await _seed_foreign(db)


async def _reach(client: AsyncClient, item_id: UUID) -> tuple[int, int, bool]:
    detail = await client.get(f"{BASE}/items/{item_id}")
    versions = await client.get(f"{BASE}/items/{item_id}/versions")
    listed = {row["id"] for row in (await client.get(f"{BASE}/items")).json()}
    return detail.status_code, versions.status_code, str(item_id) in listed


@pytest.mark.parametrize("visibility", ["public", "unlisted"])
@pytest.mark.parametrize("publication", ["none", "approved"])
async def test_other_company_cannot_see_or_install_a_public_item(
    client: AsyncClient,
    db: AsyncSession,
    test_app: FastAPI,
    visibility: str,
    publication: str,
) -> None:
    item = await add_item(db, visibility=visibility)
    item.tenant_publication = publication
    await db.commit()
    with _as_foreigner(test_app):
        detail, versions, listed = await _reach(client, item.id)
        install = await client.post(f"{BASE}/items/{item.id}/install", json={})
    assert (detail, versions, listed) == (404, 404, False)
    assert install.status_code == 404


async def test_same_company_other_organization_keeps_legacy_reach(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    item = await add_item(db, visibility="public")
    with acting_as(test_app, OUTSIDER, OTHER_ORG_ID):
        detail, _versions, listed = await _reach(client, item.id)
    assert (detail, listed) == (200, True)


async def test_platform_items_stay_visible_to_every_company(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    item = await add_item(db, is_system=True)
    with _as_foreigner(test_app):
        detail, _versions, listed = await _reach(client, item.id)
    assert (detail, listed) == (200, True)
