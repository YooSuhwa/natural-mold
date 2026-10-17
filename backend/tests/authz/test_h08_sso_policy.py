"""H-08: SSO required, break-glass administrators and email-first routing (plan 10.10)."""

from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.auth.sso.policy import SsoPolicy, store_sso_policy
from app.models.organization import Tenant, TenantRoleGrant
from app.models.user import User
from tests.authz.d04_support import OTHER_USER_ID, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, make_admin
from tests.authz.h08_flows import audit_actions
from tests.authz.h08_support import DOMAIN, seed_connection, verified_domain
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID

POLICY = f"{TENANT}/sso-policy"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession):
    await seed_scope(db)
    for user_id in (TEST_USER_ID, OTHER_USER_ID):
        user = await db.get(User, user_id)
        assert user is not None
        user.hashed_password = hash_password(PASSWORD)
    await db.commit()


async def _require_sso(db: AsyncSession, break_glass: tuple[UUID, ...]) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    store_sso_policy(tenant, SsoPolicy("required", break_glass))
    await db.commit()


async def _password_login(client: AsyncClient, email: str):
    return await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


async def test_required_needs_connection_domain_and_break_glass_admin(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    await make_admin(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        body = {"mode": "required", "break_glass_user_ids": [str(TEST_USER_ID)]}
        assert (await client.put(POLICY, json=body)).json()["error"]["code"] == (
            "sso_policy_incomplete"
        )
        await seed_connection(db)
        await verified_domain(db)
        no_admin = {"mode": "required", "break_glass_user_ids": [str(OTHER_USER_ID)]}
        assert (await client.put(POLICY, json=no_admin)).status_code == 409
        assert (await client.put(POLICY, json={"mode": "required"})).status_code == 409
        three = [str(TEST_USER_ID), str(OTHER_USER_ID), str(OTHER_USER_ID)]
        assert (
            await client.put(POLICY, json={"mode": "required", "break_glass_user_ids": three})
        ).status_code == 422
        saved = await client.put(POLICY, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["mode"] == "required"
        assert saved.json()["password_members"] == 1  # other@test.com
        assert [e.action for e in await audit_actions("tenant.sso_policy_update")] == [
            "tenant.sso_policy_update"
        ]


async def test_member_cannot_read_the_policy(client: AsyncClient, test_app: FastAPI) -> None:
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        assert (await client.get(POLICY)).status_code == 403


async def test_password_login_is_refused_when_sso_is_required(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    await _require_sso(db, (TEST_USER_ID,))
    response = await _password_login(raw_client, "other@test.com")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sso_required"
    assert "moldy_at" not in response.cookies
    failures = [e for e in await audit_actions("auth.login") if e.outcome == "failure"]
    assert [e.reason_code for e in failures] == ["sso_required"]


async def test_break_glass_admin_may_use_a_password_and_is_audited(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    db.add(TenantRoleGrant(tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID, role="admin"))
    await db.commit()
    await _require_sso(db, (TEST_USER_ID,))
    response = await _password_login(raw_client, "test@test.com")
    assert response.status_code == 200, response.text
    used = await audit_actions("auth.sso_break_glass_login")
    assert [(e.actor_user_id, e.tenant_id) for e in used] == [(TEST_USER_ID, TEST_TENANT_ID)]


async def test_optional_policy_keeps_password_login(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    store_sso_policy(tenant, SsoPolicy("optional"))
    await db.commit()
    assert (await _password_login(raw_client, "other@test.com")).status_code == 200


async def test_email_first_discovery_routes_only_verified_domains(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    unknown = await raw_client.post("/api/auth/sso/discover", json={"email": f"a@{DOMAIN}"})
    assert unknown.json()["method"] == "password" and unknown.json()["login_csrf"]
    connection = await seed_connection(db)
    await seed_connection(db, status="tested")  # not offered until activated
    pending = await raw_client.post("/api/auth/sso/discover", json={"email": f"a@{DOMAIN}"})
    assert pending.json()["method"] == "password"  # domain not verified yet
    await verified_domain(db)
    await _require_sso(db, (TEST_USER_ID,))
    routed = await raw_client.post("/api/auth/sso/discover", json={"email": f"A@{DOMAIN.upper()}"})
    body = routed.json()
    assert body["method"] == "sso" and body["sso_required"] is True
    assert [item["id"] for item in body["connections"]] == [str(connection)]
    assert body["connections"][0]["start_url"].endswith(f"/api/auth/sso/{connection}/start")
