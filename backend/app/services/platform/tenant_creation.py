"""Atomic SaaS operator company creation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol, assert_never, runtime_checkable
from uuid import uuid4

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_tenant_invitation_token
from app.authz import audit_actions
from app.authz.catalog_fanout import sync_organization
from app.authz.error_codes import authorization_error
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.organization import Organization, Tenant
from app.models.tenant_invitation import TenantInvitation
from app.schemas.admin_tenant import (
    AdminTenantDetail,
    PlatformTenantCreate,
    PlatformTenantCreateResult,
    TenantLimitSummary,
)
from app.schemas.tenant_invitations import TenantInvitationCreated
from app.schemas.tenant_organizations import TenantOrganizationSummary
from app.schemas.tenant_overview import TENANT_STATUS_ADAPTER
from app.services import audit_service
from app.services.tenants.identifiers import DEFAULT_ORGANIZATION_SLUG, tenant_secrets_namespace

_TENANT_SLUG_CONSTRAINT: Final = "uq_tenants_slug"
_TENANT_NAMESPACE_CONSTRAINT: Final = "uq_tenants_secrets_namespace"
type TenantCreationConstraint = Literal["uq_tenants_slug", "uq_tenants_secrets_namespace"] | None


@runtime_checkable
class _ConstraintNamed(Protocol):
    @property
    def constraint_name(self) -> str | None: ...


@runtime_checkable
class _DiagnosticCarrier(Protocol):
    @property
    def diag(self) -> _ConstraintNamed: ...


@dataclass(frozen=True, slots=True)
class PlatformTenantCreation:
    db: AsyncSession
    request: Request
    actor: CurrentUser


async def create_platform_tenant(
    creation: PlatformTenantCreation,
    payload: PlatformTenantCreate,
    authz: AuthzService | None = None,
) -> PlatformTenantCreateResult:
    """Create a company, its default organization, invite, and audit atomically."""
    with creation.db.no_autoflush:
        existing = await creation.db.scalar(select(Tenant.id).where(Tenant.slug == payload.slug))
    if existing is not None:
        raise authorization_error("duplicate_tenant_slug")

    now = datetime.now(UTC)
    tenant_id, organization_id, invitation_id = uuid4(), uuid4(), uuid4()
    token = create_tenant_invitation_token(invitation_id)
    serialized = payload.model_dump(mode="json")
    tenant = Tenant(
        id=tenant_id,
        name=payload.name,
        slug=payload.slug,
        status="active",
        limits={
            "member_limit": serialized["member_limit"],
            "monthly_spend_limit": serialized["monthly_spend_limit"],
            "concurrent_run_limit": serialized["concurrent_run_limit"],
            "allow_system_models": serialized["allow_system_models"],
        },
        settings={},
        secrets_namespace=tenant_secrets_namespace(tenant_id),
        data_keys={},
        created_at=now,
        updated_at=now,
    )
    organization = Organization(
        id=organization_id,
        tenant_id=tenant_id,
        name=payload.first_organization_name,
        slug=DEFAULT_ORGANIZATION_SLUG,
        status="active",
        is_default=True,
        settings={"default_member_role": "member"},
        created_by=creation.actor.id,
        created_at=now,
        updated_at=now,
    )
    invitation = TenantInvitation(
        id=invitation_id,
        tenant_id=tenant_id,
        email=str(payload.first_admin_email).casefold(),
        role="admin",
        status="pending",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=creation.actor.id,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )

    fanout = LifecycleChange()
    try:
        creation.db.add(tenant)
        await creation.db.flush()
        creation.db.add(organization)
        await creation.db.flush()
        creation.db.add(invitation)
        await creation.db.flush()
        await audit_service.record_event(
            creation.db,
            actor_type="user",
            actor_user_id=creation.actor.id,
            actor_email_snapshot=creation.actor.email,
            owner_user_id=creation.actor.id,
            owner_email_snapshot=creation.actor.email,
            action=audit_actions.TENANT_CREATE,
            target_type="tenant",
            target_id=tenant_id,
            target_name_snapshot=tenant.name,
            outcome="success",
            request=creation.request,
            metadata={"tenant_id": str(tenant_id), "org_id": str(organization_id)},
        )
        if authz is not None:  # E-03: platform catalog fans out to the first organization
            fanout = await sync_organization(
                creation.db, organization, creation.actor.id, mode=authz.mode
            )
        await creation.db.flush()
        await creation.db.commit()
    except IntegrityError as error:
        constraint = _constraint_name(error)
        await creation.db.rollback()
        match constraint:
            case "uq_tenants_slug":
                raise authorization_error("duplicate_tenant_slug") from error
            case "uq_tenants_secrets_namespace":
                raise authorization_error("tenant_namespace_collision") from error
            case None:
                raise
            case unreachable:
                assert_never(unreachable)
    except (AppError, SQLAlchemyError):
        await creation.db.rollback()
        raise
    if authz is not None:
        await apply_lifecycle(fanout, authz)

    return PlatformTenantCreateResult(
        tenant=AdminTenantDetail(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=TENANT_STATUS_ADAPTER.validate_python(tenant.status),
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            limits=TenantLimitSummary.from_tenant(tenant),
            organization_count=1,
            active_member_count=0,
        ),
        organization=TenantOrganizationSummary(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status="active",
            is_default=True,
            default_member_role="member",
            member_count=0,
            administrator_user_id=None,
            updated_at=organization.updated_at,
        ),
        invitation=TenantInvitationCreated(
            id=invitation_id,
            email=payload.first_admin_email,
            role="admin",
            expires_at=now + timedelta(days=7),
            created_at=now,
            status="pending",
            token=token,
        ),
    )


def _constraint_name(error: IntegrityError) -> TenantCreationConstraint:
    reported = _reported_constraint(error.orig)
    if reported is None and error.orig is not None:
        reported = _reported_constraint(error.orig.__cause__)
    if reported == _TENANT_SLUG_CONSTRAINT:
        return _TENANT_SLUG_CONSTRAINT
    if reported == _TENANT_NAMESPACE_CONSTRAINT:
        return _TENANT_NAMESPACE_CONSTRAINT
    return None


def _reported_constraint(error: BaseException | None) -> str | None:
    if isinstance(error, _ConstraintNamed):
        return error.constraint_name
    if isinstance(error, _DiagnosticCarrier) and isinstance(error.diag, _ConstraintNamed):
        return error.diag.constraint_name
    return None
