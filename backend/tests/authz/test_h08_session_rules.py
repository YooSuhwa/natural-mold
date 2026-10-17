"""H-08 hardening: session sign-in method and SSO-required refresh/login rules (M2, M3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.auth.sso.policy import SsoPolicy, store_sso_policy
from app.config import settings
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.authz.d04_support import OTHER_USER_ID, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, make_admin
from tests.authz.h08_flows import idp_installed, oidc_login, sso_error_of
from tests.authz.h08_support import (
    DOMAIN,
    MockIdp,
    make_default_org,
    seed_connection,
    verified_domain,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

PASSWORD = "correct horse battery"
OTHER_TENANT = uuid4()


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession):
    await seed_scope(db)
    await make_default_org(db)
    for user_id in (TEST_USER_ID, OTHER_USER_ID):
        user = await db.get(User, user_id)
        assert user is not None
        user.hashed_password = hash_password(PASSWORD)
    await db.commit()


async def _require(db: AsyncSession, tenant_id: UUID, break_glass: tuple[UUID, ...] = ()) -> None:
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None
    store_sso_policy(tenant, SsoPolicy("required", break_glass))
    await db.commit()


def _token(user_id: UUID, method: str | None) -> RefreshToken:
    return RefreshToken(
        user_id=user_id,
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        auth_method=method,
    )


async def test_password_session_cannot_refresh_once_sso_is_required(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    login = await raw_client.post(
        "/api/auth/login", json={"email": "other@test.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert (await raw_client.post("/api/auth/refresh")).status_code == 200
    await _require(db, TEST_TENANT_ID, (TEST_USER_ID,))
    refused = await raw_client.post("/api/auth/refresh")
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "sso_required"


async def test_switching_to_required_revokes_password_sessions_only(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI
) -> None:
    await make_admin(db)
    await seed_connection(db)
    await verified_domain(db)
    rows = [
        _token(OTHER_USER_ID, None),
        _token(OTHER_USER_ID, "password"),
        _token(OTHER_USER_ID, f"sso:{TEST_TENANT_ID}"),
        _token(TEST_USER_ID, "password"),  # break-glass keeps its session
    ]
    db.add_all(rows)
    await db.commit()
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        body = {"mode": "required", "break_glass_user_ids": [str(TEST_USER_ID)]}
        assert (await client.put(f"{TENANT}/sso-policy", json=body)).status_code == 200
    async with TestSession() as fresh:
        revoked = {
            row.auth_method: row.revoked_at is not None
            for row in await fresh.scalars(
                select(RefreshToken).where(RefreshToken.user_id == OTHER_USER_ID)
            )
        }
        kept = await fresh.get(RefreshToken, rows[3].id)
    assert revoked == {None: True, "password": True, f"sso:{TEST_TENANT_ID}": False}
    assert kept is not None and kept.revoked_at is None


async def test_break_glass_user_who_is_no_longer_admin_is_refused(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    await _require(db, TEST_TENANT_ID, (TEST_USER_ID,))  # listed, but holds no admin role
    response = await raw_client.post(
        "/api/auth/login", json={"email": "test@test.com", "password": PASSWORD}
    )
    assert response.status_code == 403
    db.add(TenantRoleGrant(tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID, role="admin"))
    await db.commit()
    allowed = await raw_client.post(
        "/api/auth/login", json={"email": "test@test.com", "password": PASSWORD}
    )
    assert allowed.status_code == 200


async def test_sso_login_through_one_company_is_refused_by_another_requiring_sso(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    await verified_domain(db)
    connection = await seed_connection(db, jit_enabled=True)
    alice = User(id=uuid4(), email=f"alice@{DOMAIN}", name="Alice", is_active=True)
    db.add(Tenant(id=OTHER_TENANT, name="B", slug="h08-b", secrets_namespace="h08-b"))
    db.add(alice)
    await db.flush()
    db.add(TenantMember(tenant_id=OTHER_TENANT, user_id=alice.id))
    await db.commit()
    await _require(db, OTHER_TENANT)
    with idp_installed(MockIdp()) as idp:
        refused = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(refused) == "sso_required"
    assert settings.cookie_name_access not in refused.cookies
    await _require(db, TEST_TENANT_ID, (TEST_USER_ID,))  # A itself requiring SSO is fine
    tenant_b = await db.get(Tenant, OTHER_TENANT)
    assert tenant_b is not None
    store_sso_policy(tenant_b, SsoPolicy("optional"))
    await db.commit()
    with idp_installed(MockIdp()) as idp:
        allowed = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(allowed) is None
