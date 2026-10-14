"""Ordinary rollback contract for operator company creation."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import anyio
import pytest
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.tenant_invitation import TenantInvitation
from app.schemas.admin_tenant import PlatformTenantCreate
from app.services.platform.tenant_creation import PlatformTenantCreation, create_platform_tenant
from tests.authz.platform_tenant_creation_fixture import (
    PlatformTenantCreationCase,
    aggregate_counts,
    creation_payload,
)
from tests.conftest import TestSession

pytest_plugins = ["tests.authz.platform_tenant_creation_fixture"]


class UnknownDriverIntegrityError(Exception):
    """Driver failure with no authoritative constraint identity."""


class ConstraintIdentityError(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


class ConstraintDiagnostic:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class DiagnosticDriverIntegrityError(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.diag = ConstraintDiagnostic(constraint_name)


DriverStyle = Literal["asyncpg", "diagnostic"]


def known_integrity_error(constraint: str, style: DriverStyle) -> IntegrityError:
    if style == "asyncpg":
        translated = UnknownDriverIntegrityError("translated asyncpg integrity error")
        translated.__cause__ = ConstraintIdentityError(constraint)
        original = translated
    else:
        original = DiagnosticDriverIntegrityError(constraint)
    return IntegrityError("INSERT INTO tenants VALUES (:name)", {"name": "safe"}, original)


async def assert_creation_tables_empty() -> None:
    async with TestSession() as observer:
        assert await observer.scalar(select(func.count()).select_from(Tenant)) == 0
        assert await observer.scalar(select(func.count()).select_from(Organization)) == 0
        assert await observer.scalar(select(func.count()).select_from(TenantInvitation)) == 0
        assert await observer.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert await observer.scalar(select(func.count()).select_from(TenantMember)) == 0
        assert await observer.scalar(select(func.count()).select_from(TenantRoleGrant)) == 0
        assert await observer.scalar(select(func.count()).select_from(OrganizationMember)) == 0
        assert await observer.scalar(select(func.count()).select_from(OrganizationRoleGrant)) == 0


async def test_duplicate_slug_preflight_is_typed_and_has_no_loser_effects(
    platform_tenant_creation_case: PlatformTenantCreationCase,
) -> None:
    case = platform_tenant_creation_case
    first = await case.client.post(
        "/api/admin/tenants", json=creation_payload(), headers=case.headers()
    )
    tenant_id = UUID(first.json()["tenant"]["id"])
    before = await aggregate_counts(tenant_id)

    duplicate = await case.client.post(
        "/api/admin/tenants", json=creation_payload(), headers=case.headers()
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_tenant_slug"
    assert await aggregate_counts(tenant_id) == before
    assert (
        await case.db.scalar(
            select(func.count()).select_from(Tenant).where(Tenant.slug == "task12-company")
        )
        == 1
    )


@pytest.mark.parametrize("attack", ["uq_tenants_slug", "uq_tenants_secrets_namespace"])
async def test_unknown_integrity_identity_cannot_be_remapped_by_rendered_text(
    platform_tenant_creation_case: PlatformTenantCreationCase,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = platform_tenant_creation_case
    original_flush = case.db.flush
    reached = anyio.Event()

    async def fail_after_flush() -> None:
        await original_flush()
        reached.set()
        raise IntegrityError(
            "INSERT INTO organizations (name) VALUES (:name)",
            {attack: f"customer supplied {attack}"},
            UnknownDriverIntegrityError(f"unknown failure mentions {attack}"),
        )

    with monkeypatch.context() as patch:
        patch.setattr(case.db, "flush", fail_after_flush)
        response = await case.client.post(
            "/api/admin/tenants",
            json=creation_payload(f"unknown-{attack.replace('_', '-')}", name=attack),
            headers=case.headers(),
        )

    assert reached.is_set()
    await assert_creation_tables_empty()
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize("style", ["asyncpg", "diagnostic"])
@pytest.mark.parametrize(
    ("constraint", "expected_status", "expected_code"),
    [
        ("uq_tenants_slug", 409, "duplicate_tenant_slug"),
        ("uq_tenants_secrets_namespace", 500, "tenant_namespace_collision"),
    ],
)
async def test_known_driver_constraint_identity_maps_approved_response(
    platform_tenant_creation_case: PlatformTenantCreationCase,
    monkeypatch: pytest.MonkeyPatch,
    style: DriverStyle,
    constraint: str,
    expected_status: int,
    expected_code: str,
) -> None:
    case = platform_tenant_creation_case
    original_flush = case.db.flush
    reached = anyio.Event()

    async def fail_after_flush() -> None:
        await original_flush()
        reached.set()
        raise known_integrity_error(constraint, style)

    with monkeypatch.context() as patch:
        patch.setattr(case.db, "flush", fail_after_flush)
        response = await case.client.post(
            "/api/admin/tenants",
            json=creation_payload(f"known-{style}-{expected_status}"),
            headers=case.headers(),
        )

    assert reached.is_set()
    await assert_creation_tables_empty()
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


async def test_unknown_integrity_error_propagates_same_identity_after_rollback(
    platform_tenant_creation_case: PlatformTenantCreationCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = platform_tenant_creation_case
    original_flush = case.db.flush
    reached = anyio.Event()
    injected = IntegrityError(
        "INSERT INTO organizations (name) VALUES (:name)",
        {"name": "uq_tenants_slug"},
        UnknownDriverIntegrityError("uq_tenants_secrets_namespace"),
    )

    async def fail_after_flush() -> None:
        await original_flush()
        reached.set()
        raise injected

    creation = PlatformTenantCreation(
        case.db,
        Request({"type": "http", "method": "POST", "path": "/api/admin/tenants"}),
        CurrentUser(
            id=case.actor_id,
            email="task12-operator@example.com",
            name="Task 12 Operator",
            is_super_user=True,
        ),
    )
    payload = PlatformTenantCreate.model_validate(creation_payload("propagate-unknown"))
    with monkeypatch.context() as patch:
        patch.setattr(case.db, "flush", fail_after_flush)
        with pytest.raises(IntegrityError) as raised:
            await create_platform_tenant(creation, payload)

    assert reached.is_set()
    assert raised.value is injected
    await assert_creation_tables_empty()


@pytest.mark.parametrize(("stage", "label"), [(2, "organization"), (3, "invitation"), (4, "audit")])
async def test_late_stage_failure_rolls_back_entire_aggregate(
    platform_tenant_creation_case: PlatformTenantCreationCase,
    monkeypatch: pytest.MonkeyPatch,
    stage: int,
    label: str,
) -> None:
    case = platform_tenant_creation_case
    original_flush = case.db.flush
    reached = anyio.Event()
    flushes = 0

    async def fail_after_flush() -> None:
        nonlocal flushes
        flushes += 1
        await original_flush()
        if flushes == stage:
            reached.set()
            raise SQLAlchemyError(f"injected {label} stage failure")

    with monkeypatch.context() as patch:
        patch.setattr(case.db, "flush", fail_after_flush)
        response = await case.client.post(
            "/api/admin/tenants",
            json=creation_payload(f"rollback-{label}"),
            headers=case.headers(),
        )

    assert reached.is_set()
    assert response.status_code == 500
    await assert_creation_tables_empty()
