from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.organization import Organization, Tenant, TenantMember
from app.models.user import User
from app.schemas.admin_tenant import AdminTenantMetadata
from app.schemas.tenant_invitations import TenantInvitationView
from app.schemas.tenant_members import (
    TenantMemberAdminView,
    TenantMemberProjection,
    TenantMemberSummary,
)
from app.schemas.tenant_organizations import (
    TenantOrganizationProjection,
    TenantOrganizationSummary,
)
from app.schemas.tenant_overview import TenantOverview


@dataclass(frozen=True, slots=True)
class InvitationFixture:
    id: UUID
    email: str
    role: Literal["admin", "auditor"] | None
    expires_at: datetime
    created_at: datetime
    status: Literal["pending", "accepted", "revoked", "expired"]
    token_hash: str


def _records() -> tuple[Tenant, Organization, TenantMember, User, InvitationFixture]:
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid4(),
        name="Visible Company",
        slug="visible-company",
        status="active",
        limits={"secret_limit": "reserved-limit-secret"},
        settings={"secret_setting": "reserved-setting-secret"},
        secrets_namespace="private-secret-namespace",
        data_keys={"key": "data-key-secret"},
        created_at=now,
        updated_at=now,
    )
    organization = Organization(
        id=uuid4(),
        tenant_id=tenant.id,
        name="Visible Organization",
        slug="visible-organization",
        status="active",
        is_default=False,
        settings={"private": "org-setting-secret"},
        monthly_spend_limit=0,
        created_at=now,
        updated_at=now,
    )
    user = User(
        id=uuid4(),
        email="member@example.com",
        name="Visible Member",
        hashed_password="password-hash-secret",
        password_reset_token="password-reset-secret",
        created_at=now,
    )
    member = TenantMember(
        tenant_id=tenant.id,
        user_id=user.id,
        status="active",
        created_at=now,
    )
    invitation = InvitationFixture(
        id=uuid4(),
        email="invitee@example.com",
        role="admin",
        expires_at=now + timedelta(days=7),
        created_at=now,
        status="pending",
        token_hash="token-hash-secret",
    )
    return tenant, organization, member, user, invitation


def test_company_schemas_never_expose_internal_fields() -> None:
    tenant, organization, member, user, invitation = _records()
    member_projection = TenantMemberProjection(member=member, user=user, roles=("auditor",))
    models = (
        TenantOverview.from_tenant(tenant, access="auditor"),
        TenantOrganizationSummary.from_projection(
            TenantOrganizationProjection(
                organization=organization,
                member_count=1,
                administrator_user_id=user.id,
            )
        ),
        TenantMemberSummary.from_projection(member_projection),
        TenantMemberAdminView.from_projection(
            TenantMemberProjection(member=member, user=user, roles=("admin",))
        ),
        TenantInvitationView.from_record(invitation),
        AdminTenantMetadata.from_tenant(tenant),
    )

    rendered = str([model.model_dump(mode="json") for model in models])
    for forbidden in (
        "secrets_namespace",
        "data_keys",
        "settings",
        "token_hash",
        "hashed_password",
        "password_reset_token",
        "private-secret-namespace",
        "data-key-secret",
        "reserved-setting-secret",
        "token-hash-secret",
        "password-hash-secret",
        "password-reset-secret",
        "org-setting-secret",
    ):
        assert forbidden not in rendered


def test_auditor_member_projection_redacts_email() -> None:
    _, _, member, user, _ = _records()
    auditor = TenantMemberSummary.from_projection(
        TenantMemberProjection(member=member, user=user, roles=("auditor",))
    ).model_dump()
    admin = TenantMemberAdminView.from_projection(
        TenantMemberProjection(member=member, user=user, roles=("admin",))
    ).model_dump()
    assert "email" not in auditor
    assert admin["email"] == "member@example.com"


def test_response_models_are_frozen_and_forbid_extra_fields() -> None:
    tenant, _, _, _, _ = _records()
    overview = TenantOverview.from_tenant(tenant, access="member")
    with pytest.raises(ValidationError):
        TenantOverview.model_validate({**overview.model_dump(), "secrets_namespace": "secret"})
    with pytest.raises(ValidationError):
        overview.name = "Changed"


def test_organization_projection_uses_explicit_aggregates() -> None:
    _, organization, _, _, _ = _records()
    summary = TenantOrganizationSummary.from_projection(
        TenantOrganizationProjection(
            organization=organization,
            member_count=0,
            administrator_user_id=None,
        )
    )
    assert summary.member_count == 0
