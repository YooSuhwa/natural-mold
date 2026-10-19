"""Cursor paging and company/org scopes for audit reads (plan 14.1: me, org, company, all).

``org``/``tenant`` need an ``AuditScopeFilter`` the caller built after its own permission
check (``can_view_audit`` / ``can_view_tenant_audit``) and fail closed without one.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, or_

from app.models.audit_event import AuditEvent

AuditScope = Literal["mine", "org", "tenant", "all"]


@dataclass(frozen=True)
class AuditEventPage:
    items: list[AuditEvent]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AuditScopeFilter:
    """Company rows, optionally narrowed to one organization (always both for ``org``)."""

    tenant_id: uuid.UUID
    org_id: uuid.UUID | None = None


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


def tenant_scope_conditions(scope: AuditScope, scope_filter: AuditScopeFilter | None) -> list[Any]:
    """``org``/``tenant`` rows; fails closed without a filter the caller checked."""
    if scope_filter is None or (scope == "org" and scope_filter.org_id is None):
        raise PermissionError(f"scope={scope} requires a checked scope filter")
    conditions: list[Any] = [AuditEvent.tenant_id == scope_filter.tenant_id]
    if scope_filter.org_id is not None:
        conditions.append(AuditEvent.org_id == scope_filter.org_id)
    return conditions


def cursor_condition(cursor: str | None) -> Any | None:
    decoded = _decode_cursor(cursor)
    if decoded is None:
        return None
    created_at, event_id = decoded
    return or_(
        AuditEvent.created_at < created_at,
        and_(AuditEvent.created_at == created_at, AuditEvent.id < event_id),
    )


def page_of(rows: list[AuditEvent], limit: int) -> AuditEventPage:
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1]) if len(rows) > limit and items else None
    return AuditEventPage(items=items, next_cursor=next_cursor)
