"""The configured development account has an explicit, repeatable organization."""

from __future__ import annotations

from typing import Literal, assert_never

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.seed.e2e_scope import E2E_ORG_ID, E2E_TENANT_ID
from app.seed.e2e_user import seed_e2e_user


@pytest.fixture(autouse=True)
def enable_development_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "e2e_seed_user_enabled", True)
    monkeypatch.setattr(settings, "e2e_user_email", "scoped-e2e@example.test")
    monkeypatch.setattr(settings, "e2e_user_password", "test-password-for-explicit-scope")


async def test_seeded_account_resolves_reserved_company_and_org(db: AsyncSession) -> None:
    # Given: a development database without any organization enrollment.
    user = await seed_e2e_user(db)
    assert user is not None
    # When: normal request-boundary organization resolution runs for the seeded user.
    actor = CurrentUser(
        id=user.id, email=user.email, name=user.name, is_super_user=user.is_super_user
    )
    context = await resolve_org_context(db, actor, cookie=None)
    # Then: membership, company and preference refer to the explicit E2E scope.
    assert (context.tenant_id, context.org_id, context.user_id) == (
        E2E_TENANT_ID,
        E2E_ORG_ID,
        user.id,
    )
    assert user.last_active_org_id == E2E_ORG_ID


async def test_repeated_seed_preserves_one_membership_and_explicit_admin_roles(
    db: AsyncSession,
) -> None:
    # Given: the same configured seed account is provisioned once.
    first = await seed_e2e_user(db)
    assert first is not None
    await db.commit()
    # When: startup seeds the same account again.
    second = await seed_e2e_user(db)
    await db.commit()
    # Then: fixed fixture identities and role grants are not duplicated.
    assert second is not None and second.id == first.id
    assert await db.scalar(select(func.count()).select_from(TenantMember)) == 1
    assert await db.scalar(select(func.count()).select_from(OrganizationMember)) == 1
    assert await db.scalar(select(TenantRoleGrant.role)) == "admin"
    assert await db.scalar(select(OrganizationRoleGrant.role)) == "admin"


@pytest.mark.parametrize("scope_kind", ["tenant", "organization", "membership", "org_membership"])
async def test_seed_does_not_reactivate_suspended_scope(
    db: AsyncSession, scope_kind: Literal["tenant", "organization", "membership", "org_membership"]
) -> None:
    # Given: an explicitly provisioned scope is later suspended by administration.
    user = await seed_e2e_user(db)
    assert user is not None
    tenant = await db.get(Tenant, E2E_TENANT_ID)
    org = await db.get(Organization, E2E_ORG_ID)
    member = await db.get(TenantMember, (E2E_TENANT_ID, user.id))
    org_member = await db.get(OrganizationMember, (E2E_ORG_ID, user.id))
    assert tenant is not None and org is not None and member is not None
    assert org_member is not None
    match scope_kind:
        case "tenant":
            tenant.status = "suspended"
        case "organization":
            org.status = "suspended"
        case "membership":
            member.status = "suspended"
        case "org_membership":
            org_member.status = "removed"
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    # When: startup attempts to reuse the configured account.
    with pytest.raises(AppError) as captured:
        await seed_e2e_user(db)
    # Then: startup cannot silently undo the administrative boundary.
    assert captured.value.code == "e2e_scope_unavailable"


@pytest.mark.parametrize("production", [False, True])
async def test_disabled_seed_creates_no_company_or_org(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, production: bool
) -> None:
    # Given: production or a development installation with seed explicitly disabled.
    monkeypatch.setattr(settings, "app_env", "production" if production else "dev")
    monkeypatch.setattr(settings, "e2e_seed_user_enabled", production)
    # When: the seed entrypoint runs.
    user = await seed_e2e_user(db)
    # Then: the reserved fixture company and org do not exist.
    assert user is None
    assert await db.get(Tenant, E2E_TENANT_ID) is None
    assert await db.get(Organization, E2E_ORG_ID) is None
