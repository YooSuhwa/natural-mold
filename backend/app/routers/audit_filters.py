"""Shared audit list query parameters for the org and company audit routes (14.1, H-03)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.routers.audit import to_response
from app.schemas.audit import AuditEventPageResponse
from app.services import audit_service
from app.services.audit_listing import AuditScopeFilter


@dataclass(frozen=True, slots=True)
class AuditFilters:
    limit: int
    cursor: str | None
    action: str | None
    target_type: str | None
    outcome: str | None
    actor_user_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    request_id: str | None
    trace_id: str | None
    run_id: str | None
    created_from: datetime | None
    created_to: datetime | None


async def list_scoped(
    db: AsyncSession,
    user: CurrentUser,
    filters: AuditFilters,
    scope_filter: AuditScopeFilter,
    *,
    mask_client: bool,
) -> AuditEventPageResponse:
    """Org (``org_id`` set) or company page; the caller has checked the permission."""
    page = await audit_service.list_events(
        db,
        viewer_user_id=user.id,
        viewer_is_super_user=False,
        scope="org" if scope_filter.org_id is not None else "tenant",
        limit=filters.limit,
        cursor=filters.cursor,
        action=filters.action,
        target_type=filters.target_type,
        outcome=filters.outcome,
        actor_user_id=filters.actor_user_id,
        owner_user_id=filters.owner_user_id,
        request_id_value=filters.request_id,
        trace_id=filters.trace_id,
        run_id=filters.run_id,
        created_from=filters.created_from,
        created_to=filters.created_to,
        scope_filter=scope_filter,
    )
    return AuditEventPageResponse(
        items=[to_response(item, mask_client=mask_client) for item in page.items],
        next_cursor=page.next_cursor,
    )


def audit_filters(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    actor_user_id: uuid.UUID | None = Query(default=None),
    owner_user_id: uuid.UUID | None = Query(default=None),
    request_id: str | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
) -> AuditFilters:
    return AuditFilters(
        limit,
        cursor,
        action,
        target_type,
        outcome,
        actor_user_id,
        owner_user_id,
        request_id,
        trace_id,
        run_id,
        created_from,
        created_to,
    )
