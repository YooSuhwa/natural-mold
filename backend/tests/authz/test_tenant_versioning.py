"""Tenant and organization optimistic locking share timestamp semantics."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.exceptions import AppError
from app.models.organization import Organization, Tenant
from app.services.tenants.versioning import (
    bump_timestamp,
    etag,
    require_current_version,
)


def test_tenant_and_organization_etags_use_the_same_timestamp_contract() -> None:
    # Given: a tenant and organization persisted at distinct known UTC timestamps.
    tenant_time = datetime(2026, 10, 13, tzinfo=UTC)
    organization_time = tenant_time + timedelta(seconds=1)
    tenant = Tenant(
        name="Tenant",
        slug="tenant",
        secrets_namespace="tenants/example",
        updated_at=tenant_time,
    )
    organization = Organization(
        tenant_id=uuid4(), name="Organization", slug="organization", updated_at=organization_time
    )
    # When: their ETags are rendered and supplied back for comparison.
    require_current_version(tenant.updated_at, etag(tenant_time))
    require_current_version(organization.updated_at, etag(organization_time))
    # Then: each keeps its exact quoted UTC timestamp representation.
    assert (etag(tenant.updated_at), etag(organization.updated_at)) == (
        '"2026-10-13T00:00:00+00:00"',
        '"2026-10-13T00:00:01+00:00"',
    )


@pytest.mark.parametrize("supplied", [None, "not-an-etag", '"2026-10-12T00:00:00+00:00"'])
def test_tenant_version_rejects_missing_malformed_and_stale_if_match(supplied: str | None) -> None:
    # Given: a tenant with one known persisted version.
    tenant = Tenant(
        name="Tenant",
        slug="tenant",
        secrets_namespace="tenants/example",
        updated_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
    # When: the request supplies a missing, malformed, or stale version.
    with pytest.raises(AppError) as raised:
        require_current_version(tenant.updated_at, supplied)
    # Then: product concurrency remains the exact stale-version 409 response.
    assert (raised.value.status, raised.value.code) == (409, "stale_version")


def test_bump_timestamp_strictly_advances_tenant_version() -> None:
    # Given: a future tenant timestamp to prove the microsecond floor is honored.
    original = datetime.now(UTC) + timedelta(days=1)
    tenant = Tenant(
        name="Tenant",
        slug="tenant",
        secrets_namespace="tenants/example",
        updated_at=original,
    )
    # When: a company mutation advances its timestamp.
    tenant.updated_at = bump_timestamp(tenant.updated_at)
    # Then: the ETag version is strictly newer even if the wall clock is older.
    assert tenant.updated_at > original
