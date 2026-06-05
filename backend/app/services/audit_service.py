from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.audit_event import AuditEvent

logger = logging.getLogger(__name__)

AuditScope = Literal["mine", "all"]
AuditOutcome = Literal["success", "failure", "denied", "skipped"]


@dataclass(frozen=True)
class AuditEventPage:
    items: list[AuditEvent]
    next_cursor: str | None


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


def _encode_cursor(row: AuditEvent) -> str:
    payload = {"created_at": row.created_at.isoformat(), "id": str(row.id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    return datetime.fromisoformat(payload["created_at"]), uuid.UUID(payload["id"])


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
) -> AuditEvent:
    row = AuditEvent(
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


async def record_event_best_effort(**kwargs: Any) -> None:
    try:
        async with async_session() as db:
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
) -> AuditEventPage:
    if scope == "all" and not viewer_is_super_user:
        raise PermissionError("scope=all requires super_user")

    stmt = select(AuditEvent)
    conditions = []
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

    decoded = _decode_cursor(cursor)
    if decoded:
        created_at, event_id = decoded
        conditions.append(
            or_(
                AuditEvent.created_at < created_at,
                and_(AuditEvent.created_at == created_at, AuditEvent.id < event_id),
            )
        )

    if conditions:
        stmt = stmt.where(*conditions)

    safe_limit = min(max(limit, 1), 100)
    result = await db.execute(
        stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(
            safe_limit + 1
        )
    )
    rows = list(result.scalars().all())
    items = rows[:safe_limit]
    next_cursor = _encode_cursor(items[-1]) if len(rows) > safe_limit and items else None
    return AuditEventPage(items=items, next_cursor=next_cursor)
