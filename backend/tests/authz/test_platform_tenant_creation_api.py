"""Ordinary HTTP contract for atomic operator company creation."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, Tenant
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from tests.authz.platform_tenant_creation_fixture import (
    PlatformTenantCreationCase,
    TenantAggregateCounts,
    aggregate_counts,
    creation_payload,
)

pytest_plugins = ["tests.authz.platform_tenant_creation_fixture"]


async def test_operator_create_commits_safe_complete_aggregate(
    platform_tenant_creation_case: PlatformTenantCreationCase,
) -> None:
    case = platform_tenant_creation_case
    response = await case.client.post(
        "/api/admin/tenants", json=creation_payload(), headers=case.headers()
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"tenant", "organization", "invitation"}
    assert set(body["tenant"]) == {
        "id",
        "name",
        "slug",
        "status",
        "created_at",
        "updated_at",
        "limits",
        "organization_count",
        "active_member_count",
    }
    assert set(body["organization"]) == {
        "id",
        "name",
        "slug",
        "status",
        "is_default",
        "default_member_role",
        "member_count",
        "administrator_user_id",
        "updated_at",
    }
    assert set(body["invitation"]) == {
        "id",
        "email",
        "role",
        "expires_at",
        "created_at",
        "status",
        "token",
    }
    rendered = response.text
    assert "secrets_namespace" not in rendered and "token_hash" not in rendered
    assert body["organization"]["slug"] == "default"
    assert body["organization"]["is_default"] is True
    assert body["organization"]["default_member_role"] == "member"
    assert body["organization"]["member_count"] == 0
    assert body["organization"]["administrator_user_id"] is None
    assert body["tenant"]["organization_count"] == 1
    assert body["tenant"]["active_member_count"] == 0
    assert body["invitation"]["role"] == "admin"
    assert body["invitation"]["token"] is not None

    tenant_id = UUID(body["tenant"]["id"])
    aggregate = await aggregate_counts(tenant_id)
    assert aggregate == TenantAggregateCounts(1, 1, 1, 1, 0, 0, 0, 0)
    with case.db.no_autoflush:
        tenant = await case.db.get(Tenant, tenant_id, populate_existing=True)
        organization = await case.db.scalar(
            select(Organization).where(Organization.tenant_id == tenant_id)
        )
        invitation = await case.db.scalar(
            select(TenantInvitation).where(TenantInvitation.tenant_id == tenant_id)
        )
        audit = await case.db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "tenant.create", AuditEvent.target_id == str(tenant_id)
            )
        )
    assert tenant is not None and tenant.secrets_namespace == f"tenants/{tenant.id}"
    assert organization is not None and organization.created_by == case.actor_id
    assert invitation is not None and invitation.created_by == case.actor_id
    assert invitation.expires_at - invitation.created_at == timedelta(days=7)
    assert invitation.token_hash == hashlib.sha256(body["invitation"]["token"].encode()).hexdigest()
    assert audit is not None
    assert audit.event_metadata == {"tenant_id": str(tenant.id), "org_id": str(organization.id)}
    private = await case.client.get(f"/api/tenants/{tenant.id}")
    assert private.status_code == 404
    assert private.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("mode", "operator", "expected"),
    [("single_tenant", True, 404), ("multi_tenant", False, 403)],
)
async def test_create_gate_requires_saas_operator(
    platform_tenant_creation_case: PlatformTenantCreationCase,
    mode: str,
    operator: bool,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = platform_tenant_creation_case
    monkeypatch.setattr(settings, "deployment_mode", mode)
    actor = await case.db.get(User, case.actor_id)
    assert actor is not None
    actor.is_super_user = operator
    await case.db.commit()

    response = await case.client.post(
        "/api/admin/tenants", json=creation_payload(), headers=case.headers()
    )

    assert response.status_code == expected
    assert await case.db.scalar(select(func.count()).select_from(Tenant)) == 0


async def test_create_requires_matching_csrf(
    platform_tenant_creation_case: PlatformTenantCreationCase,
) -> None:
    response = await platform_tenant_creation_case.client.post(
        "/api/admin/tenants", json=creation_payload()
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"


@pytest.mark.parametrize("slug", ["abc", "a" * 40])
async def test_create_accepts_slug_boundaries(
    platform_tenant_creation_case: PlatformTenantCreationCase, slug: str
) -> None:
    response = await platform_tenant_creation_case.client.post(
        "/api/admin/tenants",
        json=creation_payload(slug),
        headers=platform_tenant_creation_case.headers(),
    )

    assert response.status_code == 201
    assert response.json()["tenant"]["slug"] == slug


@pytest.mark.parametrize("slug", ["ab", "a" * 41, "-abc", "abc-", "ABC", "회사"])
async def test_create_rejects_noncanonical_slug(
    platform_tenant_creation_case: PlatformTenantCreationCase, slug: str
) -> None:
    response = await platform_tenant_creation_case.client.post(
        "/api/admin/tenants",
        json=creation_payload(slug),
        headers=platform_tenant_creation_case.headers(),
    )

    assert response.status_code == 422
    assert (
        await platform_tenant_creation_case.db.scalar(select(func.count()).select_from(Tenant)) == 0
    )


@pytest.mark.parametrize("extra", [{"secrets_namespace": "leak"}, {"first_org_slug": "custom"}])
async def test_create_rejects_internal_identifier_inputs(
    platform_tenant_creation_case: PlatformTenantCreationCase, extra: dict[str, str]
) -> None:
    response = await platform_tenant_creation_case.client.post(
        "/api/admin/tenants",
        json={**creation_payload(), **extra},
        headers=platform_tenant_creation_case.headers(),
    )

    assert response.status_code == 422


async def test_create_allows_duplicate_names(
    platform_tenant_creation_case: PlatformTenantCreationCase,
) -> None:
    case = platform_tenant_creation_case
    first = await case.client.post(
        "/api/admin/tenants", json=creation_payload("same-name-a"), headers=case.headers()
    )
    second = await case.client.post(
        "/api/admin/tenants", json=creation_payload("same-name-b"), headers=case.headers()
    )

    assert first.status_code == second.status_code == 201


async def test_create_openapi_publishes_canonical_slug(
    platform_tenant_creation_case: PlatformTenantCreationCase,
) -> None:
    response = await platform_tenant_creation_case.client.get("/openapi.json")
    schema = response.json()["components"]["schemas"]["PlatformTenantCreate"]["properties"]["slug"]

    assert schema["minLength"] == 3
    assert schema["maxLength"] == 40
    assert schema["pattern"] == "^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"
