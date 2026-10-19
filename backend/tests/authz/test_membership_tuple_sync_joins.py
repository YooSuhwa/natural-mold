"""Invitation, SSO/JIT and company-creation joins queue their tuples (7.2.1 "구성원 추가")."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.membership_sync import MembershipSync
from app.models.user import User
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.h08_flows import idp_installed, oidc_login
from tests.authz.h08_support import (
    DOMAIN,
    MockIdp,
    make_default_org,
    seed_connection,
    verified_domain,
)
from tests.authz.membership_sync_testkit import (
    assert_parity,
    outbox_rows,
    reconciled_store,
    user_tuples,
)
from tests.authz.org_invitation_accept_fixture import pending_invitation
from tests.authz.tenant_invitation_fixture import pending_tenant_invitation
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.platform_tenant_creation_fixture import PlatformTenantCreationCase

pytest_plugins = [
    "tests.authz.org_context_fixture",
    "tests.authz.platform_tenant_creation_fixture",
]


async def test_logged_in_organization_invitation_accept_projects_member_role_and_group(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation = await pending_invitation(db, raw_client, org_fixture)
    store = await reconciled_store()
    accepted = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    assert accepted.status_code == 200, accepted.text
    await assert_parity(store)
    org = f"organization:{org_fixture.first}"
    assert {
        ("member", org),
        ("builder", org),
        ("member", f"group:{invitation.group_id}"),
        ("member", f"tenant:{org_fixture.tenant}"),
    } <= user_tuples(store, invitation.recipient_id)


async def test_company_invitation_accept_projects_company_member_and_role(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation = await pending_tenant_invitation(
        db, raw_client, tenant_id=org_fixture.tenant, created_by=org_fixture.user.id
    )
    store = await reconciled_store()
    accepted = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    assert accepted.status_code == 200, accepted.text
    await assert_parity(store)
    company = f"tenant:{org_fixture.tenant}"
    assert {("member", company), ("admin", company)} <= user_tuples(store, invitation.recipient_id)


async def test_jit_sso_login_projects_company_and_mapped_organization_role(
    db: AsyncSession, raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_scope(db)
    await make_default_org(db)
    await verified_domain(db)
    mapping = {"eng": {"org_id": str(OTHER_ORG_ID), "role": "builder"}}
    connection = await seed_connection(db, jit_enabled=True, group_role_mapping=mapping)
    store = await reconciled_store()
    with idp_installed(MockIdp(claims={"groups": ["eng"]})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert response.status_code == 302, response.text
    user = await db.scalar(select(User).where(User.email == f"alice@{DOMAIN}"))
    assert user is not None
    await assert_parity(store)
    org = f"organization:{OTHER_ORG_ID}"
    assert user_tuples(store, user.id) == {
        ("member", f"tenant:{TEST_TENANT_ID}"),
        ("member", org),
        ("builder", org),
    }
    # A repeat login that changes nothing captures (reads) and queues nothing.
    queued = await outbox_rows()

    async def no_capture(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an unchanged SSO login must not capture the company")

    monkeypatch.setattr(MembershipSync, "capture", no_capture)
    with idp_installed(MockIdp(claims={"groups": ["eng"]})) as idp:
        again = await oidc_login(raw_client, idp, connection)
    assert again.status_code == 302, again.text
    assert "sso_error" not in again.headers["location"]
    assert await outbox_rows() == queued


async def test_platform_company_creation_projects_the_default_organization_tenant(
    platform_tenant_creation_case: PlatformTenantCreationCase,
) -> None:
    from tests.authz.platform_tenant_creation_fixture import creation_payload

    case = platform_tenant_creation_case
    store = await reconciled_store()
    created = await case.client.post(
        "/api/admin/tenants", json=creation_payload(), headers=case.headers()
    )
    assert created.status_code == 201, created.text
    body = created.json()
    tenant, org = UUID(body["tenant"]["id"]), UUID(body["organization"]["id"])
    await assert_parity(store)
    stored = {(t.user, t.relation, t.object) for t in store.stored()}
    assert (f"tenant:{tenant}", "tenant", f"organization:{org}") in stored
