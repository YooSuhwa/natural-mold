"""Native company invitation cleanup removes only explicitly owned rows."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from app.authz.registration_queries import DEFAULT_COMPANY_ID
from app.models.organization import Tenant
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from tests.authz.tenant_invitation_native_fixture import (
    native_company,
    seed_native_invitation,
)

if TYPE_CHECKING:
    from tests.authz.tenant_invitation_native_fixture import NativeInvitationDatabase

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_invitation_native_fixture"]


class ControlledPostSeedFailure(RuntimeError):
    """Signal the cleanup path after committed fixture rows exist."""


async def test_cleanup_preserves_foreign_same_email_and_default_company(
    tenant_invitation_native_database: NativeInvitationDatabase,
) -> None:
    database = tenant_invitation_native_database
    foreign_user_id = uuid4()
    foreign_tenant_id = uuid4()
    foreign_email = "native-cleanup-shared@example.com"
    default_created = False
    async with database.primary() as db:
        default = await db.get(Tenant, DEFAULT_COMPANY_ID)
        if default is None:
            default = Tenant(
                id=DEFAULT_COMPANY_ID,
                name="Cleanup Default",
                slug=f"cleanup-default-{uuid4().hex[:10]}",
                secrets_namespace=f"tenants/{DEFAULT_COMPANY_ID}",
            )
            db.add(default)
            default_created = True
        db.add(
            User(
                id=foreign_user_id,
                email=foreign_email,
                name="Foreign Same Email",
                hashed_password="foreign-hash",
                is_active=True,
            )
        )
        db.add(
            Tenant(
                id=foreign_tenant_id,
                name="Foreign Same Prefix",
                slug=f"native-invite-{foreign_tenant_id.hex[:12]}",
                secrets_namespace=f"tenants/{foreign_tenant_id}",
            )
        )
        await db.commit()
    try:
        async with native_company(
            database.primary,
            invited_email=foreign_email,
            existing_invitee=False,
        ) as fixture:
            fixture_tenant_id = fixture.tenant_id
            assert foreign_user_id not in fixture.owned.user_ids
        async with database.primary() as db:
            assert await db.get(Tenant, fixture_tenant_id) is None
            assert await db.get(User, foreign_user_id) is not None
            assert await db.get(Tenant, foreign_tenant_id) is not None
            assert await db.get(Tenant, DEFAULT_COMPANY_ID) is not None
    finally:
        async with database.primary() as db:
            foreign = await db.get(User, foreign_user_id)
            if foreign is not None:
                await db.delete(foreign)
            foreign_tenant = await db.get(Tenant, foreign_tenant_id)
            if foreign_tenant is not None:
                await db.delete(foreign_tenant)
            if default_created:
                default = await db.get(Tenant, DEFAULT_COMPANY_ID)
                if default is not None:
                    await db.delete(default)
            await db.commit()


async def test_post_seed_failure_cleans_tracked_company_invitation_and_users(
    tenant_invitation_native_database: NativeInvitationDatabase,
) -> None:
    database = tenant_invitation_native_database
    tenant_id: UUID | None = None
    actor_id: UUID | None = None
    invitee_id: UUID | None = None
    invitation_id: UUID | None = None

    async def seed_then_fail() -> None:
        nonlocal tenant_id, actor_id, invitee_id, invitation_id
        async with native_company(
            database.primary, invited_email="native-cleanup-failure@example.com"
        ) as fixture:
            invitation, _ = await seed_native_invitation(database.primary, fixture)
            tenant_id = fixture.tenant_id
            actor_id = fixture.actor.id
            invitee_id = fixture.invited_user_id
            invitation_id = invitation.id
            raise ControlledPostSeedFailure

    with pytest.raises(ControlledPostSeedFailure):
        await seed_then_fail()
    assert tenant_id is not None
    assert actor_id is not None
    assert invitee_id is not None
    assert invitation_id is not None
    async with database.primary() as db:
        assert await db.get(Tenant, tenant_id) is None
        assert await db.get(TenantInvitation, invitation_id) is None
        assert await db.get(User, actor_id) is None
        assert await db.get(User, invitee_id) is None
