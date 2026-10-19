"""``GET /api/access-requests?box=`` (plan 9.7 "접근 요청", S18; H-01).

* ``outbox``: the caller's own requests, every status.
* ``inbox``: pending requests of others on resources the caller may share
  (``can_share``; legacy: the owner).
* ``handled``: approved, denied and expired requests on those resources.

Rows are the current organization's only, newest first, keyset cursor
``created_at|id``. Inbox/handled candidates are confirmed with ``filter_visible`` batch
by batch (8.10) until ``limit + 1`` confirmed rows are found or the candidates run out;
``has_more`` and the cursor come from confirmed rows only, so a page never reveals
requests the caller cannot act on. Reads write nothing: a pending row past
``expires_at`` is listed (and shown) as expired without being updated.
"""

import base64
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.access_request_rules import now
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService
from app.authz.share_targets import SHARE_TYPES
from app.models.access_request import AccessRequest
from app.models.user import User
from app.schemas.access_request import (
    AccessRequestPage,
    AccessRequestView,
    RequestBox,
    RequestUser,
)

_BATCH = 200


def _box_filter(box: RequestBox, ctx: OrgContext) -> ColumnElement[bool]:
    live = and_(AccessRequest.status == "pending", AccessRequest.expires_at > now())
    if box == "outbox":
        return AccessRequest.requester_id == ctx.user_id
    others = AccessRequest.requester_id != ctx.user_id
    if box == "inbox":
        return and_(others, live)
    closed = AccessRequest.status.in_(("approved", "denied", "expired"))
    return and_(others, or_(closed, and_(AccessRequest.status == "pending", ~live)))


def effective_status(row: AccessRequest) -> str:
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite returns naive UTC
        expires = expires.replace(tzinfo=UTC)
    if row.status == "pending" and expires <= now():
        return "expired"
    return row.status


def _encode(row: AccessRequest) -> str:
    raw = f"{row.created_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode(cursor: str) -> tuple[datetime, UUID]:
    try:
        stamp, _, rid = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(stamp), UUID(rid)
    except (ValueError, UnicodeDecodeError) as error:
        raise authorization_error("invalid_cursor") from error


async def _owned(db: AsyncSession, rows: Sequence[AccessRequest], ctx: OrgContext) -> set[str]:
    """Legacy answer: the requests on resources the caller owns in this organization."""
    found: set[str] = set()
    for name, kind in SHARE_TYPES.items():
        ids = {row.resource_id for row in rows if row.resource_type == name}
        if not ids:
            continue
        model = kind.model
        owned = await db.scalars(
            select(model.id).where(
                model.id.in_(ids), model.user_id == ctx.user_id, model.org_id == ctx.org_id
            )
        )
        found.update(f"{name}:{rid}" for rid in owned)
    return found


async def _names(db: AsyncSession, rows: Sequence[AccessRequest]) -> dict[UUID, str]:
    names: dict[UUID, str] = {}
    for name, kind in SHARE_TYPES.items():
        ids = {row.resource_id for row in rows if row.resource_type == name}
        if ids:
            model = kind.model
            query = select(model.id, model.name).where(model.id.in_(ids))
            names.update((await db.execute(query)).tuples().all())
    return names


async def request_views(db: AsyncSession, rows: Sequence[AccessRequest]) -> list[AccessRequestView]:
    names = await _names(db, rows)
    users = {row.requester_id for row in rows}
    people = {
        user.id: user.display_name or user.name
        for user in await db.scalars(select(User).where(User.id.in_(users)))
    }
    return [
        AccessRequestView(
            id=row.id,
            resource_type=row.resource_type,  # type: ignore[arg-type]  # ck constraint
            resource_id=row.resource_id,
            resource_name=names.get(row.resource_id),
            level=row.relation,  # type: ignore[arg-type]  # requestable levels only
            reason=row.reason,
            status=effective_status(row),  # type: ignore[arg-type]  # ck constraint
            requester=RequestUser(id=row.requester_id, display_name=people.get(row.requester_id)),
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            decision_note=row.decision_note,
            granted_level=row.granted_relation,  # type: ignore[arg-type]
            created_at=row.created_at,
            expires_at=row.expires_at,
        )
        for row in rows
    ]


async def request_view(db: AsyncSession, request: AccessRequest) -> AccessRequestView:
    [view] = await request_views(db, [request])
    return view


async def _shareable(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, rows: list[AccessRequest]
) -> list[AccessRequest]:
    objs = [f"{row.resource_type}:{row.resource_id}" for row in rows]
    legacy = await _owned(db, rows, ctx)
    kept = set(
        await authz.filter_visible(
            Principal.for_user(ctx),
            "can_share",
            list(dict.fromkeys(objs)),
            legacy=[obj for obj in dict.fromkeys(objs) if obj in legacy],
        )
    )
    return [row for row, obj in zip(rows, objs, strict=True) if obj in kept]


async def list_requests(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    box: RequestBox,
    *,
    cursor: str | None,
    limit: int,
) -> AccessRequestPage:
    base = (
        select(AccessRequest)
        .where(AccessRequest.org_id == ctx.org_id, _box_filter(box, ctx))
        .order_by(AccessRequest.created_at.desc(), AccessRequest.id.desc())
    )
    position = _decode(cursor) if cursor else None
    found: list[AccessRequest] = []
    while len(found) <= limit:
        query = base
        if position is not None:
            stamp, rid = position
            query = query.where(
                or_(
                    AccessRequest.created_at < stamp,
                    and_(AccessRequest.created_at == stamp, AccessRequest.id < rid),
                )
            )
        batch = list(await db.scalars(query.limit(limit + 1 if box == "outbox" else _BATCH)))
        if not batch:
            break
        position = (batch[-1].created_at, batch[-1].id)
        found += batch if box == "outbox" else await _shareable(db, authz, ctx, batch)
        if box == "outbox" or len(batch) < _BATCH:
            break
    has_more = len(found) > limit
    rows = found[:limit]
    return AccessRequestPage(
        items=await request_views(db, rows),
        next_cursor=_encode(rows[-1]) if has_more else None,
        has_more=has_more,
    )
