from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_oversight import load_org_oversight
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context
from app.exceptions import ForbiddenError
from app.models.audit_event import AuditEvent
from app.schemas.audit import AuditEventPageResponse, AuditEventResponse, AuditScope
from app.services import audit_service
from app.services.audit_listing import AuditScopeFilter

router = APIRouter(prefix="/api/audit-events", tags=["audit"])


async def org_scope_filter(
    request: Request, db: AsyncSession, user: CurrentUser
) -> AuditScopeFilter:
    """``scope=org`` (14.1, H-03): the current organization, ``can_view_audit``."""
    context = await get_org_context(request, user, db)
    organization = await load_org_oversight(db, user, context.org_id, "can_view_audit")
    return AuditScopeFilter(tenant_id=organization.tenant_id, org_id=organization.id)


def to_response(event: AuditEvent, *, mask_client: bool = False) -> AuditEventResponse:
    """``mask_client``: org-level views (14.1, H-03) hide the client IP and user agent."""
    return AuditEventResponse(
        id=event.id,
        actor_type=event.actor_type,
        actor_user_id=event.actor_user_id,
        actor_api_key_id=event.actor_api_key_id,
        actor_email_snapshot=event.actor_email_snapshot,
        actor_label=event.actor_label,
        owner_user_id=event.owner_user_id,
        owner_email_snapshot=event.owner_email_snapshot,
        action=event.action,
        target_type=event.target_type,
        target_id=event.target_id,
        target_name_snapshot=event.target_name_snapshot,
        target_owner_user_id=event.target_owner_user_id,
        outcome=event.outcome,
        reason_code=event.reason_code,
        reason_message=event.reason_message,
        request_id=event.request_id,
        trace_id=event.trace_id,
        run_id=event.run_id,
        ip_address=None if mask_client else event.ip_address,
        user_agent=None if mask_client else event.user_agent,
        metadata=event.event_metadata,
        created_at=event.created_at,
    )


@router.get("", response_model=AuditEventPageResponse)
@authz_policy(SELF)
async def list_audit_events(
    request: Request,
    scope: AuditScope = Query(default="mine"),
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
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> AuditEventPageResponse:
    scope_filter = await org_scope_filter(request, db, user) if scope == "org" else None
    try:
        page = await audit_service.list_events(
            db,
            viewer_user_id=user.id,
            viewer_is_super_user=user.is_super_user,
            scope=scope,
            limit=limit,
            cursor=cursor,
            action=action,
            target_type=target_type,
            outcome=outcome,
            actor_user_id=actor_user_id,
            owner_user_id=owner_user_id,
            request_id_value=request_id,
            trace_id=trace_id,
            run_id=run_id,
            created_from=created_from,
            created_to=created_to,
            scope_filter=scope_filter,
        )
    except PermissionError as exc:
        raise ForbiddenError("forbidden", "권한이 없습니다") from exc

    return AuditEventPageResponse(
        items=[to_response(item, mask_client=scope == "org") for item in page.items],
        next_cursor=page.next_cursor,
    )
