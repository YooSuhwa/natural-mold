"""Explicit native test company selection through the real CSRF-protected API."""

from collections.abc import Mapping

from sqlalchemy import select

from app.authz.org_context import OrgContext
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User
from tests.authz.test_registration_policy_integration import SignupApp


async def select_second_company(
    stack: SignupApp, headers: Mapping[str, str], email: str
) -> OrgContext:
    async with stack.sessions() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        company = Tenant(
            name="Other writer company", slug="other-writer", secrets_namespace="native-writer"
        )
        db.add(company)
        await db.flush()
        organization = Organization(
            tenant_id=company.id, name="Other writer org", slug="other-writer"
        )
        db.add(organization)
        await db.flush()
        db.add_all(
            [
                TenantMember(tenant_id=company.id, user_id=user.id),
                OrganizationMember(org_id=organization.id, tenant_id=company.id, user_id=user.id),
            ]
        )
        await db.commit()
        context = OrgContext(user_id=user.id, org_id=organization.id, tenant_id=company.id)
    selected = await stack.client.post(
        "/api/me/active-org", headers=headers, json={"org_id": str(context.org_id)}
    )
    assert selected.status_code == 200, selected.text
    return context
