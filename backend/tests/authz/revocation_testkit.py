"""Shared fixtures for the C-08 revocation marker / deny overlay tests."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.pending_markers import PendingMarkers, pending_markers
from app.authz.revocation_overlay import DbRevocationOverlay
from app.authz.revocations import RevocationScope, record_revocation
from app.authz.service import AuthzService
from app.models.organization import Organization, Tenant
from tests.authz.authz_service_testkit import NOW, USER, RecordingClient
from tests.conftest import TestSession

SUBJECT = f"user:{USER}"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_pending_hint() -> Iterator[None]:
    """The process hint is global; each test starts with an unknown count."""
    pending_markers.reset()
    yield
    pending_markers.reset()


async def seed_org(db: AsyncSession, org_id: UUID) -> UUID:
    """Persist a tenant + organization with ``org_id``; returns the tenant id."""
    tenant = Tenant(name=f"t-{org_id}", slug=f"t-{org_id}", secrets_namespace=f"t-{org_id}")
    db.add(tenant)
    await db.flush()
    db.add(Organization(id=org_id, tenant_id=tenant.id, name="o", slug=f"o-{uuid4()}"))
    await db.commit()
    return tenant.id


async def revoke(
    db: AsyncSession,
    org_id: UUID,
    tenant_id: UUID,
    scope: RevocationScope,
    obj: str,
    subject: str = SUBJECT,
) -> UUID:
    """Record and commit a marker (step 1 of the revocation order)."""
    marker = await record_revocation(
        db, org_id=org_id, tenant_id=tenant_id, scope=scope, subject=subject, object=obj
    )
    await db.commit()
    return marker.id


def overlaid(
    client: RecordingClient,
    mode: str = "enforce",
    *,
    hint: PendingMarkers = pending_markers,
    hook: object = None,
) -> AuthzService:
    overlay = DbRevocationOverlay(TestSession, hint=hint)
    return AuthzService(
        client,
        mode=mode,  # type: ignore[arg-type]
        shadow_hook=hook,  # type: ignore[arg-type]
        clock=lambda: NOW,
        overlay=overlay,
    )
