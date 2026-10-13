"""Company invitation denials do not autoflush caller-owned pending state."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.state import InstanceState
from sqlalchemy.orm.unitofwork import UOWTransaction

from app.auth.jwt import create_tenant_invitation_token
from app.dependencies import get_db
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantMember
from app.routers.invitation_accept import router as invitation_accept_router
from tests.authz.org_context_fixture import OrgFixture
from tests.authz.tenant_invitation_fixture import seed_tenant_invitation

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.fixture
async def denial_client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def same_session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app = create_app()
    app.include_router(invitation_accept_router)
    app.dependency_overrides[get_db] = same_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://denial-test"
    ) as client:
        yield client


def _pending_unrelated(db: AsyncSession) -> Tenant:
    tenant_id = uuid4()
    row = Tenant(
        id=tenant_id,
        name="Unrelated Pending",
        slug=f"unrelated-{tenant_id.hex[:12]}",
        secrets_namespace=f"tenants/{tenant_id}",
    )
    db.add(row)
    return row


async def _audit_count(db: AsyncSession) -> int:
    with db.no_autoflush:
        return await db.scalar(select(func.count()).select_from(AuditEvent)) or 0


async def _request_without_flush(
    db: AsyncSession,
    unrelated: Tenant,
    request: AsyncGenerator[Response, None],
) -> Response:
    flushes = 0

    def count_flush(
        session: Session,
        flush_context: UOWTransaction,
        instances: Iterable[InstanceState[Tenant]] | None,
    ) -> None:
        nonlocal flushes
        del session, flush_context, instances
        flushes += 1

    before_audits = await _audit_count(db)
    event.listen(db.sync_session, "before_flush", count_flush)
    try:
        response = await anext(request)
    finally:
        event.remove(db.sync_session, "before_flush", count_flush)
        await request.aclose()
    assert flushes == 0
    assert inspect(unrelated).pending and unrelated in db.new
    assert await _audit_count(db) == before_audits
    return response


async def _post(
    client: AsyncClient,
    path: str,
    payload: dict[str, str],
    headers: dict[str, str] | None = None,
) -> AsyncGenerator[Response, None]:
    yield await client.post(path, json=payload, headers=headers)


async def test_unknown_company_accept_token_denies_before_autoflush(
    db: AsyncSession, denial_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    org_fixture.session.apply(denial_client)
    unrelated = _pending_unrelated(db)
    token = create_tenant_invitation_token(uuid4())
    response = await _request_without_flush(
        db,
        unrelated,
        _post(
            denial_client,
            "/api/invitations/accept",
            {"token": token},
            org_fixture.session.headers(),
        ),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "tenant_invitation_invalid"


async def test_unknown_company_registration_token_denies_before_autoflush(
    db: AsyncSession, denial_client: AsyncClient
) -> None:
    unrelated = _pending_unrelated(db)
    token = create_tenant_invitation_token(uuid4())
    response = await _request_without_flush(
        db,
        unrelated,
        _post(
            denial_client,
            "/api/auth/register",
            {
                "email": "unknown-company-token@example.com",
                "password": "correct horse",
                "name": "Unknown Token",
                "invitation_token": token,
            },
        ),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "tenant_invitation_invalid"


async def test_valid_hint_wrong_user_denies_after_authoritative_reread_without_flush(
    db: AsyncSession, denial_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    org_fixture.session.apply(denial_client)
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="different-user@example.com",
    )
    unrelated = _pending_unrelated(db)
    response = await _request_without_flush(
        db,
        unrelated,
        _post(
            denial_client,
            "/api/invitations/accept",
            {"token": token},
            org_fixture.session.headers(),
        ),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_invitation_email_mismatch"
    assert invitation.status == "pending" and invitation.accepted_at is None


async def test_existing_account_registration_denies_before_create_user_without_flush(
    db: AsyncSession, denial_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email=org_fixture.user.email,
    )
    unrelated = _pending_unrelated(db)
    response = await _request_without_flush(
        db,
        unrelated,
        _post(
            denial_client,
            "/api/auth/register",
            {
                "email": org_fixture.user.email,
                "password": "correct horse",
                "name": "Existing Account",
                "invitation_token": token,
            },
        ),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_exists"
    assert invitation.status == "pending" and invitation.accepted_at is None


async def test_removed_member_denial_reads_membership_without_autoflush(
    db: AsyncSession, denial_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    org_fixture.session.apply(denial_client)
    member = await db.get(TenantMember, (org_fixture.tenant, org_fixture.user.id))
    assert member is not None
    member.status = "removed"
    await db.commit()
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email=org_fixture.user.email,
    )
    unrelated = _pending_unrelated(db)
    response = await _request_without_flush(
        db,
        unrelated,
        _post(
            denial_client,
            "/api/invitations/accept",
            {"token": token},
            org_fixture.session.headers(),
        ),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invitation_membership_blocked"
    assert invitation.status == "pending" and invitation.accepted_at is None
