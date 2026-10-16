"""Organization archive/restore: default prohibition, precedence and restore window."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMember, TenantMember, TenantRoleGrant
from app.models.user import User
from app.services.orgs.versioning import etag
from tests.authz.tenant_organizations_fixture import Task7Case, seed_task7_case
from tests.conftest import register_session

pytest_plugins = [
    "tests.authz.tenant_organizations_fixture",
    "tests.authz.tenant_organization_lifecycle_app",
]
NAME = "Existing organization"


def _path(case: Task7Case, org_id: UUID, action: str) -> str:
    return f"/api/tenants/{case.tenant_id}/organizations/{org_id}/{action}"


async def _org(db: AsyncSession, org_id: UUID) -> Organization:
    db.expire_all()
    organization = await db.get(Organization, org_id)
    assert organization is not None
    return organization


async def _audits(db: AsyncSession, org_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.target_id == str(org_id))
        )
        or 0
    )


async def _add_sibling(db: AsyncSession, case: Task7Case) -> UUID:
    sibling = Organization(tenant_id=case.tenant_id, name="Sibling", slug=f"sib-{uuid4().hex}")
    db.add(sibling)
    await db.commit()
    return sibling.id


async def _archive(
    client: AsyncClient,
    case: Task7Case,
    org: Organization,
    *,
    name: str = NAME,
    stale: bool = False,
) -> Response:
    version = "2001-01-01T00:00:00+00:00" if stale else etag(org.updated_at)
    return await client.post(
        _path(case, org.id, "archive"),
        json={"confirm_name": name},
        headers={**case.auth.headers(), "If-Match": version},
    )


def _code(response: Response) -> str:
    return response.json()["error"]["code"]


async def test_nondefault_archive_then_restore_round_trip(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    await _add_sibling(db, case)
    archived = await _archive(lifecycle_client, case, await _org(db, case.org_id))
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    row = await _org(db, case.org_id)
    assert row.archived_at is not None and row.is_default is False

    restored = await lifecycle_client.post(
        _path(case, case.org_id, "restore"),
        headers={**case.auth.headers(), "If-Match": archived.headers["etag"]},
    )

    assert restored.status_code == 200 and restored.json()["status"] == "active"
    row = await _org(db, case.org_id)
    assert row.archived_at is None and row.status == "active"
    actions = set(
        await db.scalars(select(AuditEvent.action).where(AuditEvent.target_id == str(row.id)))
    )
    assert actions == {"org.archive", "org.restore"}


@pytest.mark.parametrize("legacy_archived", [False, True])
@pytest.mark.parametrize(
    ("name", "stale", "sibling"),
    [(NAME, False, True), ("Wrong", True, False), (NAME, True, True), ("Wrong", False, False)],
)
async def test_default_archive_always_forbidden_before_other_checks(
    db: AsyncSession,
    lifecycle_client: AsyncClient,
    legacy_archived: bool,
    name: str,
    stale: bool,
    sibling: bool,
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    if sibling:
        await _add_sibling(db, case)
    row = await _org(db, case.org_id)
    row.is_default = True
    if legacy_archived:
        row.status, row.archived_at = "archived", datetime.now(UTC)
    await db.commit()
    before = await _org(db, case.org_id)
    snapshot = (before.status, before.archived_at, before.updated_at, before.authz_epoch)

    response = await _archive(lifecycle_client, case, before, name=name, stale=stale)

    assert response.status_code == 409
    assert _code(response) == "default_organization_archive_forbidden"
    after = await _org(db, case.org_id)
    assert (after.status, after.archived_at, after.updated_at, after.authz_epoch) == snapshot
    assert after.is_default is True
    assert await _audits(db, case.org_id) == 0


async def test_archive_validation_precedence_matrix(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    row = await _org(db, case.org_id)

    stale = await _archive(lifecycle_client, case, row, name="Wrong", stale=True)
    mismatch = await _archive(lifecycle_client, case, row, name="Wrong")
    last = await _archive(lifecycle_client, case, row)

    assert [_code(stale), _code(mismatch), _code(last)] == [
        "stale_version",
        "organization_archive_name_mismatch",
        "last_organization",
    ]
    assert [r.status_code for r in (stale, mismatch, last)] == [409, 409, 409]
    assert (await _org(db, case.org_id)).status == "active"
    assert await _audits(db, case.org_id) == 0


async def test_unauthorized_or_foreign_requests_disclose_no_default_status(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db, access="auditor")
    row = await _org(db, case.org_id)
    row.is_default = True
    await db.commit()

    default_attempt = await _archive(lifecycle_client, case, await _org(db, case.org_id))
    missing_attempt = await lifecycle_client.post(
        _path(case, uuid4(), "archive"),
        json={"confirm_name": NAME},
        headers={**case.auth.headers(), "If-Match": etag(row.updated_at)},
    )
    foreign_tenant = await lifecycle_client.post(
        f"/api/tenants/{uuid4()}/organizations/{case.org_id}/archive",
        json={"confirm_name": NAME},
        headers=case.auth.headers(),
    )

    assert default_attempt.status_code == missing_attempt.status_code == 403
    assert _code(default_attempt) == _code(missing_attempt)
    assert foreign_tenant.status_code == 404


async def test_restore_window_conflict_and_legacy_default(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    active = await lifecycle_client.post(
        _path(case, case.org_id, "restore"),
        headers={**case.auth.headers(), "If-Match": etag((await _org(db, case.org_id)).updated_at)},
    )
    assert active.status_code == 403 and _code(active) == "organization_unavailable"

    row = await _org(db, case.org_id)
    row.status, row.archived_at = "archived", datetime.now(UTC) - timedelta(days=31)
    await db.commit()
    expired = await lifecycle_client.post(
        _path(case, case.org_id, "restore"),
        headers={**case.auth.headers(), "If-Match": etag((await _org(db, case.org_id)).updated_at)},
    )
    assert expired.status_code == 410 and _code(expired) == "organization_restore_expired"

    row = await _org(db, case.org_id)
    row.is_default, row.archived_at = True, datetime.now(UTC) - timedelta(days=29)
    await db.commit()
    legacy = await lifecycle_client.post(
        _path(case, case.org_id, "restore"),
        headers={**case.auth.headers(), "If-Match": etag((await _org(db, case.org_id)).updated_at)},
    )
    assert legacy.status_code == 200
    row = await _org(db, case.org_id)
    assert (row.status, row.is_default, row.archived_at) == ("active", True, None)


async def test_contract_has_no_successor_field(lifecycle_client: AsyncClient) -> None:
    schema = (await lifecycle_client.get("/openapi.json")).json()["components"]["schemas"]
    assert set(schema["TenantOrganizationArchive"]["properties"]) == {"confirm_name"}


def test_router_is_globally_registered_once_by_task_14() -> None:
    registry = Path(__file__).resolve().parents[2] / "app" / "router_registry.py"
    assert (
        registry.read_text().count("app.include_router(tenant_organization_lifecycle.router)") == 1
    )


async def test_archive_audit_content_and_version_advance(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    await _add_sibling(db, case)
    before = await _org(db, case.org_id)
    before_tag = etag(before.updated_at)

    archived = await _archive(lifecycle_client, case, before)
    stale_restore = await lifecycle_client.post(
        _path(case, case.org_id, "restore"),
        headers={**case.auth.headers(), "If-Match": before_tag},
    )

    assert archived.headers["etag"] != before_tag
    assert stale_restore.status_code == 409 and _code(stale_restore) == "stale_version"
    event = await db.scalar(select(AuditEvent).where(AuditEvent.target_id == str(case.org_id)))
    assert event is not None
    assert (event.action, event.outcome, event.target_type) == (
        "org.archive",
        "success",
        "organization",
    )
    assert event.event_metadata == {"tenant_id": str(case.tenant_id), "is_default": False}


async def test_admin_missing_and_foreign_org_are_identical_404(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    other = await seed_task7_case(lifecycle_client, db)
    case.auth.apply(lifecycle_client)
    responses = [
        await lifecycle_client.post(
            _path(case, target, "archive"),
            json={"confirm_name": NAME},
            headers={**case.auth.headers(), "If-Match": "x"},
        )
        for target in (uuid4(), other.org_id)
    ]

    assert [r.status_code for r in responses] == [404, 404]
    assert responses[0].json() == responses[1].json()


async def test_fixed_bootstrap_default_signup_survives_archive_attempt(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case = await seed_task7_case(lifecycle_client, db)
    db.add_all(
        (
            TenantMember(tenant_id=TENANT, user_id=case.actor_id),
            TenantRoleGrant(tenant_id=TENANT, user_id=case.actor_id, role="admin"),
        )
    )
    await db.commit()
    default = await _org(db, ORG)
    attempt = await lifecycle_client.post(
        f"/api/tenants/{TENANT}/organizations/{ORG}/archive",
        json={"confirm_name": default.name},
        headers={**case.auth.headers(), "If-Match": etag(default.updated_at)},
    )
    assert _code(attempt) == "default_organization_archive_forbidden"

    email = f"bootstrap-{uuid4().hex}@example.com"
    await register_session(lifecycle_client, email=email, name="Bootstrap signup")
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    member = await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.org_id == ORG, OrganizationMember.user_id == user.id
        )
    )
    assert member is not None and member.status == "active"
    assert (await _org(db, ORG)).status == "active"
