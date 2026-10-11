"""Declared unit-test scope for the app whose authentication is already overridden."""

from typing import Final, assert_never
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.platform_scope import PlatformScope
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser
from app.models.credential import Credential
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.skill import Skill
from app.models.user import User
from app.skills import service as skill_service
from app.skills.creation import PackageSkillCreation, TextSkillCreation

TEST_TENANT_ID: Final = uuid5(NAMESPACE_URL, "moldy/unit-test-tenant")
TEST_ORG_ID: Final = uuid5(NAMESPACE_URL, "moldy/unit-test-organization")


def unit_context(user_id: UUID) -> OrgContext:
    return OrgContext(tenant_id=TEST_TENANT_ID, org_id=TEST_ORG_ID, user_id=user_id)


async def ensure_unit_scope(db: AsyncSession, actor: CurrentUser) -> OrgContext:
    """Seed a real user/membership graph for the explicit mocked-auth unit fixture."""
    user = await db.get(User, actor.id)
    if user is None:
        user = User(
            id=actor.id,
            email=actor.email,
            name=actor.name,
            is_active=True,
            is_super_user=actor.is_super_user,
        )
        db.add(user)
        await db.flush()
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    if tenant is None:
        db.add(
            Tenant(
                id=TEST_TENANT_ID,
                name="Unit fixture company",
                slug="unit-fixture",
                secrets_namespace=f"tenants/{TEST_TENANT_ID}",
            )
        )
        await db.flush()
    organization = await db.get(Organization, TEST_ORG_ID)
    if organization is None:
        db.add(
            Organization(
                id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                name="Unit fixture organization",
                slug="unit-fixture",
            )
        )
        await db.flush()
    if await db.get(TenantMember, (TEST_TENANT_ID, user.id)) is None:
        db.add(TenantMember(tenant_id=TEST_TENANT_ID, user_id=user.id))
    if await db.get(OrganizationMember, (TEST_ORG_ID, user.id)) is None:
        db.add(OrganizationMember(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=user.id))
    await db.flush()
    return unit_context(user.id)


async def create_test_credential(
    db: AsyncSession, creation: CredentialCreation, context: OrgContext | PlatformScope
) -> Credential:
    """Prepare declared unit scope before invoking the actual strict service writer."""
    match context:
        case OrgContext():
            assert (context.org_id, context.tenant_id) == (TEST_ORG_ID, TEST_TENANT_ID)
            user = await db.get(User, context.user_id)
            assert user is not None
            actor = CurrentUser(
                id=user.id, email=user.email, name=user.name, is_super_user=user.is_super_user
            )
            await ensure_unit_scope(db, actor)
        case PlatformScope():
            pass
        case unreachable:
            assert_never(unreachable)
    return await credential_service.create(db, creation, context)


async def create_test_text_skill(
    db: AsyncSession, creation: TextSkillCreation, context: OrgContext
) -> Skill:
    """Declare the real unit membership graph before invoking the strict text writer."""
    assert (context.org_id, context.tenant_id) == (TEST_ORG_ID, TEST_TENANT_ID)
    existing = await db.get(User, context.user_id)
    await ensure_unit_scope(
        db,
        CurrentUser(
            id=context.user_id,
            email=existing.email
            if existing is not None
            else f"{context.user_id.hex}@unit-fixture.test",
            name="Unit fixture",
        ),
    )
    return await skill_service.create_text_skill(db, creation, context)


async def create_test_package_skill(
    db: AsyncSession, creation: PackageSkillCreation, context: OrgContext
) -> Skill:
    """Declare the real unit membership graph before invoking the strict package writer."""
    assert (context.org_id, context.tenant_id) == (TEST_ORG_ID, TEST_TENANT_ID)
    existing = await db.get(User, context.user_id)
    await ensure_unit_scope(
        db,
        CurrentUser(
            id=context.user_id,
            email=existing.email
            if existing is not None
            else f"{context.user_id.hex}@unit-fixture.test",
            name="Unit fixture",
        ),
    )
    return await skill_service.create_package_skill(db, creation, context)
