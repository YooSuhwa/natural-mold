from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.organization import Tenant
from app.schemas.admin_tenant import AdminTenantMetadata, PlatformTenantCreate
from app.schemas.tenant_invitations import TenantInvitationCreate


def test_operator_projection_is_explicit_metadata_only() -> None:
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid4(),
        name="Company",
        slug="company",
        status="active",
        limits={},
        secrets_namespace="tenants/private",
        settings={"private": "setting"},
        data_keys={"private": "key"},
        created_at=now,
        updated_at=now,
    )

    assert set(AdminTenantMetadata.from_tenant(tenant).model_dump()) == {
        "id",
        "name",
        "slug",
        "status",
        "created_at",
        "updated_at",
        "limits",
    }


@pytest.mark.parametrize(
    "field",
    ["member_limit", "monthly_spend_limit", "concurrent_run_limit"],
)
def test_platform_tenant_create_rejects_negative_limits(field: str) -> None:
    values = {
        "name": "Company",
        "slug": "company",
        "first_admin_email": "admin@example.com",
        "first_organization_name": "Default",
        field: Decimal("-1"),
    }
    with pytest.raises(ValidationError):
        PlatformTenantCreate.model_validate(values)


def test_platform_tenant_create_accepts_zero_limits() -> None:
    request = PlatformTenantCreate(
        name="Company",
        slug="company",
        first_admin_email="admin@example.com",
        first_organization_name="Default",
        member_limit=0,
        monthly_spend_limit=Decimal("0"),
        concurrent_run_limit=0,
    )
    assert request.member_limit == request.monthly_spend_limit == request.concurrent_run_limit == 0


@pytest.mark.parametrize("roles", [(), ("admin",), ("auditor",)])
def test_tenant_invitation_create_accepts_zero_or_one_role(
    roles: tuple[str, ...],
) -> None:
    request = TenantInvitationCreate.model_validate(
        {"email": "invitee@example.com", "roles": roles}
    )
    assert request.roles == roles


def test_tenant_invitation_create_rejects_two_distinct_roles() -> None:
    with pytest.raises(ValidationError):
        TenantInvitationCreate.model_validate(
            {
                "email": "invitee@example.com",
                "roles": ("admin", "auditor"),
            }
        )
