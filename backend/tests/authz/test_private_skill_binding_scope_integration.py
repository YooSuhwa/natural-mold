"""Actual m83 binding API rejects same-user cross-company credential selection."""

import pytest
from sqlalchemy import select

from app.authz.org_context import OrgContext, resolve_org_context
from app.config import settings
from app.credentials import service as credentials
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser
from app.models.marketplace import SkillCredentialBinding
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.skill import Skill
from app.models.user import User
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_skill_binding_requires_matching_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        # Given one user with private credential roots in two companies.
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-binding@example.com",
                "name": "Binding fixture",
                "password": "native-binding-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-binding@example.com"))
            assert user is not None
            actor = CurrentUser(id=user.id, email=user.email, name=user.name)
            assert user.last_active_org_id is not None
            first = await resolve_org_context(db, actor, cookie=str(user.last_active_org_id))
            tenant = Tenant(
                name="Other binding company", slug="other-binding", secrets_namespace="binding"
            )
            db.add(tenant)
            await db.flush()
            organization = Organization(
                tenant_id=tenant.id, name="Other binding org", slug="other-binding"
            )
            db.add(organization)
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=tenant.id, user_id=user.id),
                    OrganizationMember(
                        org_id=organization.id, tenant_id=tenant.id, user_id=user.id
                    ),
                ]
            )
            await db.flush()
            second = OrgContext(user_id=user.id, org_id=organization.id, tenant_id=tenant.id)
            skill = Skill(
                user_id=user.id,
                org_id=first.org_id,
                tenant_id=first.tenant_id,
                name="Native binding fixture",
                slug="native-binding-fixture",
                kind="text",
                credential_requirements=[
                    {
                        "key": "credential",
                        "definition_key": "openai",
                        "required": True,
                        "scope": "user",
                        "injection": "env",
                    }
                ],
            )
            db.add(skill)
            await db.flush()
            own = await credentials.create(
                db, CredentialCreation("openai", "Own fixture", {"api_key": "dummy-fixture"}), first
            )
            foreign = await credentials.create(
                db,
                CredentialCreation("openai", "Foreign fixture", {"api_key": "dummy-fixture"}),
                second,
            )
            await db.commit()
            skill_id, own_id, foreign_id = skill.id, own.id, foreign.id

        # When the same user's other-company credential is proposed through HTTP.
        rejected = await stack.client.put(
            f"/api/skills/{skill_id}/credential-bindings/credential",
            headers=headers,
            json={"credential_id": str(foreign_id)},
        )
        # Then it is uniformly missing and no binding row is created.
        assert rejected.status_code == 404, rejected.text
        async with stack.sessions() as db:
            assert (
                await db.scalar(
                    select(SkillCredentialBinding).where(
                        SkillCredentialBinding.skill_id == skill_id
                    )
                )
                is None
            )
        accepted = await stack.client.put(
            f"/api/skills/{skill_id}/credential-bindings/credential",
            headers=headers,
            json={"credential_id": str(own_id)},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["credential_id"] == str(own_id)
