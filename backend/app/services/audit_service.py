from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    TenantScoped,
    async_session,
    platform_session,
    session_tenant,
    tenant_session,
)
from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.services.audit_listing import (
    AuditEventPage,
    AuditScope,
    AuditScopeFilter,
    cursor_condition,
    page_of,
    tenant_scope_conditions,
)
from app.services.audit_org_scope import event_org_scope

logger = logging.getLogger(__name__)

AuditOutcome = Literal["success", "failure", "denied", "skipped"]


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: max(limit - 3, 0)] + "..."


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return _truncate(xff.split(",")[0].strip() or None, 64)
    return _truncate(request.client.host if request.client else None, 64)


def user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    return _truncate(request.headers.get("user-agent"), 255)


def request_id(request: Request | None) -> str | None:
    if request is None:
        return None
    return _truncate(getattr(request.state, "request_id", None), 80)


def sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    from app.marketplace.redaction import redact_keys

    redacted = redact_keys(metadata)
    if isinstance(redacted, dict):
        return redacted
    return {"value": redacted}


def sanitize_reason(message: str | None) -> str | None:
    return _truncate(message, 500)


async def record_event(
    db: AsyncSession,
    *,
    actor_type: str,
    action: str,
    target_type: str,
    outcome: AuditOutcome | str,
    actor_user_id: uuid.UUID | None = None,
    actor_api_key_id: uuid.UUID | None = None,
    actor_email_snapshot: str | None = None,
    actor_label: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    owner_email_snapshot: str | None = None,
    target_id: str | uuid.UUID | None = None,
    target_name_snapshot: str | None = None,
    target_owner_user_id: uuid.UUID | None = None,
    reason_code: str | None = None,
    reason_message: str | None = None,
    request: Request | None = None,
    request_id_override: str | None = None,
    trace_id: str | None = None,
    run_id: str | uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent_value: str | None = None,
    metadata: dict[str, Any] | None = None,
    tenant_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
) -> AuditEvent:
    tenant_id, org_id = await event_org_scope(
        db,
        tenant_id=tenant_id or session_tenant(db),
        org_id=org_id,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )
    row = AuditEvent(
        tenant_id=tenant_id,
        org_id=org_id,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        actor_email_snapshot=_truncate(actor_email_snapshot, 255),
        actor_label=_truncate(actor_label, 200),
        owner_user_id=owner_user_id,
        owner_email_snapshot=_truncate(owner_email_snapshot, 255),
        action=action,
        target_type=target_type,
        target_id=_truncate(str(target_id), 128) if target_id is not None else None,
        target_name_snapshot=_truncate(target_name_snapshot, 200),
        target_owner_user_id=target_owner_user_id,
        outcome=outcome,
        reason_code=_truncate(reason_code, 80),
        reason_message=sanitize_reason(reason_message),
        request_id=_truncate(request_id_override or request_id(request), 80),
        trace_id=_truncate(trace_id, 128),
        run_id=_truncate(str(run_id), 128) if run_id is not None else None,
        ip_address=ip_address or client_ip(request),
        user_agent=user_agent_value or user_agent(request),
        event_metadata=sanitize_metadata(metadata),
    )
    db.add(row)
    return row


async def record_self_event(
    db: AsyncSession,
    user: CurrentUser,
    *,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID | None = None,
    target_name: str | None = None,
    outcome: AuditOutcome | str = "success",
    reason_code: str | None = None,
    reason_message: str | None = None,
    request: Request | None = None,
    run_id: str | uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Self-action convenience wrapper — actor == owner == target owner.

    Absorbs the seven identity kwargs every self-owned mutation repeats
    (BE-D3). Anything where the actor differs from the resource owner
    (operator actions on system resources, marketplace installs of another
    user's item, ...) must keep calling :func:`record_event` explicitly.
    """

    return await record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name_snapshot=target_name,
        target_owner_user_id=user.id,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
        request=request,
        run_id=run_id,
        metadata=metadata,
    )


async def record_event_best_effort(*, tenant: TenantScoped, **kwargs: Any) -> None:
    await _record_best_effort(lambda: tenant_session(tenant, maker=async_session), kwargs)


async def record_platform_event_best_effort(**kwargs: Any) -> None:
    """Platform audit (8.9): access denied before any company was resolved."""
    await _record_best_effort(
        lambda: platform_session("audit.platform_event", maker=async_session), kwargs
    )


async def _record_best_effort(
    open_session: Callable[[], AsyncSession], kwargs: dict[str, Any]
) -> None:
    try:
        async with open_session() as db:
            await record_event(db, **kwargs)
            await db.commit()
    except Exception:
        logger.warning("audit event write failed", exc_info=True)


async def list_events(
    db: AsyncSession,
    *,
    viewer_user_id: uuid.UUID,
    viewer_is_super_user: bool,
    scope: AuditScope,
    limit: int,
    cursor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    outcome: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
    request_id_value: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    scope_filter: AuditScopeFilter | None = None,
) -> AuditEventPage:
    if scope == "all" and not viewer_is_super_user:
        raise PermissionError("scope=all requires super_user")

    stmt = select(AuditEvent)
    conditions = []
    if scope in ("org", "tenant"):
        conditions.extend(tenant_scope_conditions(scope, scope_filter))
    if scope == "mine":
        conditions.append(
            or_(
                AuditEvent.owner_user_id == viewer_user_id,
                AuditEvent.actor_user_id == viewer_user_id,
                AuditEvent.target_owner_user_id == viewer_user_id,
            )
        )
    if action:
        conditions.append(AuditEvent.action == action)
    if target_type:
        conditions.append(AuditEvent.target_type == target_type)
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    if actor_user_id:
        conditions.append(AuditEvent.actor_user_id == actor_user_id)
    if owner_user_id:
        conditions.append(AuditEvent.owner_user_id == owner_user_id)
    if request_id_value:
        conditions.append(AuditEvent.request_id == request_id_value)
    if trace_id:
        conditions.append(AuditEvent.trace_id == trace_id)
    if run_id:
        conditions.append(AuditEvent.run_id == run_id)
    if created_from:
        conditions.append(AuditEvent.created_at >= created_from)
    if created_to:
        conditions.append(AuditEvent.created_at <= created_to)

    if (after := cursor_condition(cursor)) is not None:
        conditions.append(after)

    if conditions:
        stmt = stmt.where(*conditions)

    safe_limit = min(max(limit, 1), 100)
    result = await db.execute(
        stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(safe_limit + 1)
    )
    return page_of(list(result.scalars().all()), safe_limit)
