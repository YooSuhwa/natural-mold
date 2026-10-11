"""Actual m83 catalog and uninstall deny cross-company installed-child references."""

import pytest
from sqlalchemy import select

from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace import install_service, origin_service
from app.marketplace.install.common import _existing_installation
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.skill import Skill
from app.models.user import User
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_foreign_installed_child_is_absent_and_cannot_be_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-child@example.com",
                "name": "Child fixture",
                "password": "native-child-password",
            },
        )
        assert registered.status_code == 201, registered.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-child@example.com"))
            assert user is not None and user.last_active_org_id is not None
            actor = CurrentUser(id=user.id, email=user.email, name=user.name)
            context = await resolve_org_context(db, actor, cookie=str(user.last_active_org_id))
            tenant = Tenant(
                name="Other child company", slug="other-child", secrets_namespace="child-fixture"
            )
            db.add(tenant)
            await db.flush()
            other = Organization(tenant_id=tenant.id, name="Other child org", slug="other-child")
            db.add(other)
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=tenant.id, user_id=user.id),
                    OrganizationMember(org_id=other.id, tenant_id=tenant.id, user_id=user.id),
                ]
            )
            item = MarketplaceItem(
                resource_type="skill",
                name="System fixture",
                slug="native-child-system",
                is_system=True,
                owner_user_id=None,
                org_id=None,
                tenant_id=None,
                visibility="system",
                status="published",
            )
            skill = Skill(
                user_id=user.id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Installed fixture",
                slug="native-child",
                kind="text",
                credential_requirements=[],
            )
            db.add_all([item, skill])
            await db.flush()
            version = MarketplaceVersion(
                item_id=item.id,
                version_label="fixture-1",
                version_number=1,
                resource_type="skill",
                payload_kind="skill_package",
                content_hash="a" * 64,
            )
            db.add(version)
            await db.flush()
            installation = MarketplaceInstallation(
                item_id=item.id,
                version_id=version.id,
                resource_type="skill",
                user_id=user.id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                installed_skill_id=skill.id,
            )
            db.add(installation)
            await db.flush()
            own = await origin_service.derive_installation_summary(db, item=item, context=context)
            assert own.installed is True and own.installed_resource_id == skill.id
            # Given a malformed persisted cross-company child reference.
            skill.org_id, skill.tenant_id = other.id, tenant.id
            await db.commit()
            # When metadata, reuse and resource deletion target the original installation.
            foreign = await origin_service.derive_installation_summary(
                db, item=item, context=context
            )
            assert foreign.installed is False and foreign.installed_resource_id is None
            with pytest.raises(AppError) as reuse:
                await _existing_installation(db, item=item, user=actor, context=context)
            with pytest.raises(AppError) as removal:
                await install_service.delete_installation(
                    db,
                    installation_id=installation.id,
                    user=actor,
                    context=context,
                    delete_resource=True,
                )
            # Then neither mutation is allowed and the foreign private child remains intact.
            assert reuse.value.status == removal.value.status == 404
            await db.refresh(skill)
            await db.refresh(installation)
            assert skill.org_id == other.id and skill.tenant_id == tenant.id
            assert installation.install_status == "active"
