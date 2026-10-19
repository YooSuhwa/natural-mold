"""Organization creation preserves atomicity across administrator variants."""

import hashlib
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import Organization, TenantMember
from app.models.organization_group import OrgInvitation
from app.models.user import User
from tests.authz.tenant_organizations_fixture import seed_task7_case

pytest_plugins = ["tests.authz.tenant_organizations_fixture"]


async def test_duplicate_names_allocate_suffixes_without_actor_membership(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    results = []
    for _ in range(2):
        response = await task7_client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json={"name": "Repeated name"},
            headers=case.auth.headers(),
        )
        assert response.status_code == 201
        results.append(response.json())
    assert [row["organization"]["slug"] for row in results] == [
        "repeated-name",
        "repeated-name-2",
    ]
    assert [row["administrator"] for row in results] == [
        {"kind": "none"},
        {"kind": "none"},
    ]


async def test_email_administrator_returns_token_but_stores_only_hash(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    response = await task7_client.post(
        f"/api/tenants/{case.tenant_id}/organizations",
        json={"name": "Invited", "administrator": {"email": "invitee@example.com"}},
        headers=case.auth.headers(),
    )
    assert response.status_code == 201
    outcome = response.json()["administrator"]
    assert outcome["kind"] == "invitation"
    assert outcome["invitation"]["role"] == "admin"
    token = outcome["invitation"]["token"]
    row = await db.get(OrgInvitation, UUID(outcome["invitation"]["id"]))
    assert row is not None
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token != row.token_hash


@pytest.mark.parametrize("target_kind", ["foreign", "inactive"])
async def test_invalid_selected_user_rolls_back_every_create_row(
    db: AsyncSession, task7_client: AsyncClient, target_kind: str
) -> None:
    case = await seed_task7_case(task7_client, db)
    target = User(email=f"invalid-{uuid4().hex}@example.com", name="Invalid target")
    db.add(target)
    await db.flush()
    if target_kind == "inactive":
        db.add(TenantMember(tenant_id=case.tenant_id, user_id=target.id))
        target.is_active = False
    await db.commit()
    orgs_before = await db.scalar(
        select(func.count())
        .select_from(Organization)
        .where(Organization.tenant_id == case.tenant_id)
    )
    audits_before = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.actor_user_id == case.actor_id)
    )
    response = await task7_client.post(
        f"/api/tenants/{case.tenant_id}/organizations",
        json={"name": "Must roll back", "administrator": {"user_id": str(target.id)}},
        headers=case.auth.headers(),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    orgs_after = await db.scalar(
        select(func.count())
        .select_from(Organization)
        .where(Organization.tenant_id == case.tenant_id)
    )
    audits_after = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.actor_user_id == case.actor_id)
    )
    assert (orgs_after, audits_after) == (orgs_before, audits_before)


async def test_create_payload_rejects_unknown_role_and_dual_administrator(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    invalid = (
        {"name": "Invalid", "default_member_role": "admin"},
        {
            "name": "Invalid",
            "administrator": {"user_id": str(case.target_id), "email": "both@example.com"},
        },
    )
    for payload in invalid:
        response = await task7_client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json=payload,
            headers=case.auth.headers(),
        )
        assert response.status_code == 422
