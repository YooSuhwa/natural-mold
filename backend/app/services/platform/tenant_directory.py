"""Metadata-only SaaS operator company directory."""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.dependencies import CurrentUser
from app.models.organization import Organization, Tenant, TenantMember
from app.schemas.admin_tenant import (
    AdminTenantDetail,
    PlatformTenantMetadataPatch,
    TenantLimitSummary,
)
from app.schemas.tenant_overview import TENANT_STATUS_ADAPTER, TenantStatus
from app.services import audit_service
from app.services.tenants.pagination import (
    TenantCursor,
    TenantCursorScope,
    decode_cursor,
    encode_cursor,
)
from app.services.tenants.versioning import bump_timestamp, require_current_version

_LIMIT_FIELDS = (
    "member_limit",
    "monthly_spend_limit",
    "concurrent_run_limit",
    "allow_system_models",
)
_SAFE_TENANT_COLUMNS: Final = load_only(
    Tenant.id,
    Tenant.name,
    Tenant.slug,
    Tenant.status,
    Tenant.limits,
    Tenant.created_at,
    Tenant.updated_at,
)


class PlatformTenantPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AdminTenantDetail, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class PlatformTenantDirectoryQuery:
    status: TenantStatus | None
    search: str | None
    cursor: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class PlatformTenantMutation:
    db: AsyncSession
    request: Request
    actor: CurrentUser
    tenant_id: UUID
    expected_version: str | None


async def list_platform_tenants(
    db: AsyncSession, query: PlatformTenantDirectoryQuery
) -> PlatformTenantPage:
    """Return a stable page containing only safe company metadata and aggregates."""
    search = query.search.strip().lower() if query.search and query.search.strip() else None
    scope = TenantCursorScope(
        list_kind="platform_tenants",
        tenant_id=None,
        filter_key=f"{query.status or ''}\x1f{search or ''}",
    )
    cursor = decode_cursor(query.cursor, scope)
    statement = _aggregate_statement()
    if query.status is not None:
        statement = statement.where(Tenant.status == query.status)
    if search is not None:
        statement = statement.where(
            or_(
                func.lower(Tenant.name).contains(search, autoescape=True),
                func.lower(Tenant.slug).contains(search, autoescape=True),
            )
        )
    if cursor is not None:
        statement = statement.where(
            or_(
                Tenant.created_at < cursor.sort_value,
                and_(Tenant.created_at == cursor.sort_value, Tenant.id < cursor.position),
            )
        )
    rows = (
        (
            await db.execute(
                statement.order_by(Tenant.created_at.desc(), Tenant.id.desc()).limit(
                    query.limit + 1
                )
            )
        )
        .tuples()
        .all()
    )
    visible = rows[: query.limit]
    has_more = len(rows) > query.limit
    next_cursor = None
    if has_more:
        last = visible[-1][0]
        next_cursor = encode_cursor(
            TenantCursor(scope=scope, sort_value=last.created_at, position=last.id)
        )
    return PlatformTenantPage(
        items=tuple(_detail(*row) for row in visible),
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_platform_tenant(db: AsyncSession, tenant_id: UUID) -> AdminTenantDetail:
    """Return one metadata-only company detail."""
    return await _load_detail(db, tenant_id)


async def update_platform_tenant(
    mutation: PlatformTenantMutation, payload: PlatformTenantMetadataPatch
) -> AdminTenantDetail:
    """Apply allowlisted metadata and its audit event in one transaction."""
    tenant = await mutation.db.scalar(
        select(Tenant)
        .options(_SAFE_TENANT_COLUMNS)
        .where(Tenant.id == mutation.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tenant is None:
        raise authorization_error("not_found")
    require_current_version(tenant.updated_at, mutation.expected_version)

    changed_fields: list[str] = []
    if (
        "name" in payload.model_fields_set
        and payload.name is not None
        and payload.name != tenant.name
    ):
        tenant.name = payload.name
        changed_fields.append("name")
    limits = dict(tenant.limits)
    serialized = payload.model_dump(mode="json", exclude_unset=True)
    for field in _LIMIT_FIELDS:
        if field in payload.model_fields_set and limits.get(field) != serialized[field]:
            limits[field] = serialized[field]
            changed_fields.append(field)
    if not changed_fields:
        return await _load_detail(mutation.db, tenant.id)

    try:
        tenant.limits = limits
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        changed_fields.sort()
        await audit_service.record_event(
            mutation.db,
            actor_type="user",
            actor_user_id=mutation.actor.id,
            owner_user_id=mutation.actor.id,
            action=audit_actions.TENANT_UPDATE,
            target_type="tenant",
            target_id=tenant.id,
            target_name_snapshot=tenant.name,
            outcome="success",
            request=mutation.request,
            metadata={"tenant_id": str(tenant.id), "changed_fields": changed_fields},
        )
        await mutation.db.commit()
    except SQLAlchemyError:
        await mutation.db.rollback()
        raise
    return await _load_detail(mutation.db, tenant.id)


def _aggregate_statement() -> Select[tuple[Tenant, int, int]]:
    organization_count = (
        select(func.count())
        .select_from(Organization)
        .where(Organization.tenant_id == Tenant.id, Organization.status == "active")
        .correlate(Tenant)
        .scalar_subquery()
    )
    active_member_count = (
        select(func.count())
        .select_from(TenantMember)
        .where(TenantMember.tenant_id == Tenant.id, TenantMember.status == "active")
        .correlate(Tenant)
        .scalar_subquery()
    )
    return select(Tenant, organization_count, active_member_count).options(_SAFE_TENANT_COLUMNS)


async def _load_detail(db: AsyncSession, tenant_id: UUID) -> AdminTenantDetail:
    row = (
        (await db.execute(_aggregate_statement().where(Tenant.id == tenant_id)))
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise authorization_error("not_found")
    return _detail(*row)


def _detail(tenant: Tenant, organization_count: int, active_member_count: int) -> AdminTenantDetail:
    return AdminTenantDetail(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        status=TENANT_STATUS_ADAPTER.validate_python(tenant.status),
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        limits=TenantLimitSummary.from_tenant(tenant),
        organization_count=organization_count,
        active_member_count=active_member_count,
    )
