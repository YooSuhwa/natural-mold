"""Read side of notifications (plan 14.2, S25; H-02).

Scope of the bell: the caller's own rows of the current company that belong to the
current organization, plus company-level rows (``org_id`` NULL) while the caller is a
company admin, within the 90-day retention. Pages are newest first with a keyset
cursor ``created_at|id``. Every listed target is re-checked against the recipient's
current access (the type's 8.4 ``min_visible``; legacy: the owner in this organization):
a target they can no longer see keeps only its type — no id, no name, no link. An
OpenFGA outage hides every target rather than failing the bell. A ``grant.received``
target also keeps its name (not its link) while the share is still active: the sharer
disclosed it, even where the type's legacy rule (owner only) does not yet open it.
"""

import base64
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.candidates import granted_resource_ids
from app.authz.error_codes import authorization_error
from app.authz.notifications import RETENTION, now
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService
from app.authz.share_targets import SHARE_TYPES
from app.authz.tenant_scoping import tenant_role_query
from app.exceptions import AppError
from app.models.authz import ResourceGrant
from app.models.notification import Notification
from app.models.organization import OrganizationMember
from app.models.user import User
from app.schemas.notification import (
    NotificationActor,
    NotificationPage,
    NotificationTarget,
    NotificationView,
    UnreadCount,
)


async def _scope(db: AsyncSession, ctx: OrgContext) -> ColumnElement[bool]:
    roles = set(await db.scalars(tenant_role_query(ctx.tenant_id, ctx.user_id)))
    org = Notification.org_id == ctx.org_id
    return and_(
        Notification.user_id == ctx.user_id,
        Notification.tenant_id == ctx.tenant_id,
        Notification.created_at >= now() - RETENTION,
        or_(org, Notification.org_id.is_(None)) if "admin" in roles else org,
    )


def _encode(row: Notification) -> str:
    raw = f"{row.created_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode(cursor: str) -> tuple[datetime, UUID]:
    try:
        stamp, _, rid = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(stamp), UUID(rid)
    except (ValueError, UnicodeDecodeError) as error:
        raise authorization_error("invalid_cursor") from error


async def _accessible(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, rows: Sequence[Notification]
) -> set[str]:
    kept: set[str] = set()
    principal = Principal.for_user(ctx)
    for name, kind in SHARE_TYPES.items():
        ids = {row.target_id for row in rows if row.target_type == name and row.target_id}
        if not ids:
            continue
        model = kind.model
        owned = await db.scalars(
            select(model.id).where(
                model.id.in_(ids), model.user_id == ctx.user_id, model.org_id == ctx.org_id
            )
        )
        objs = [f"{name}:{rid}" for rid in ids]
        legacy = [f"{name}:{rid}" for rid in owned]
        try:
            kept.update(
                await authz.filter_visible(principal, kind.min_visible, objs, legacy=legacy)
            )
        except AppError as error:
            if error.code != denials.authz_unavailable().code:
                raise
    return kept


async def _still_shared(
    db: AsyncSession, ctx: OrgContext, rows: Sequence[Notification]
) -> set[str]:
    kept: set[str] = set()
    for name in SHARE_TYPES:
        ids = {
            row.target_id
            for row in rows
            if row.kind == "grant.received" and row.target_type == name and row.target_id
        }
        if ids:
            query = granted_resource_ids(name, ctx).where(ResourceGrant.resource_id.in_(ids))
            kept.update(f"{name}:{rid}" for rid in await db.scalars(query))
    return kept


async def _views(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, rows: Sequence[Notification]
) -> list[NotificationView]:
    kept = await _accessible(db, authz, ctx, rows)
    shared = await _still_shared(db, ctx, rows)
    actors = {row.actor_id for row in rows if row.actor_id}
    people = {
        user.id: user.display_name or user.name
        for user in await db.scalars(select(User).where(User.id.in_(actors)))
    }
    views: list[NotificationView] = []
    for row in rows:
        payload = {k: v for k, v in (row.payload or {}).items() if k != "target_name"}
        target, link = None, row.link
        if row.target_type is not None:
            # H-06 usage warnings name the recipient's own limit, not a shared resource.
            own = row.target_type == "usage_limit"
            key = f"{row.target_type}:{row.target_id}"
            disclosed = row.kind == "grant.received" and key in shared
            visible = own or key in kept or disclosed
            target = NotificationTarget(type=row.target_type, accessible=visible)
            if visible:
                target.id, target.name = row.target_id, (row.payload or {}).get("target_name")
            if not (own or key in kept):
                link = None
        actor = None
        if row.actor_id is not None and (target is None or target.accessible):
            actor = NotificationActor(id=row.actor_id, display_name=people.get(row.actor_id))
        views.append(
            NotificationView(
                id=row.id,
                kind=row.kind,
                org_id=row.org_id,
                target=target,
                actor=actor,
                payload=payload,
                link=link,
                read_at=row.read_at,
                created_at=row.created_at,
            )
        )
    return views


async def list_notifications(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    *,
    cursor: str | None,
    limit: int,
    unread: bool,
) -> NotificationPage:
    query = select(Notification).where(await _scope(db, ctx))
    if unread:
        query = query.where(Notification.read_at.is_(None))
    if cursor:
        stamp, rid = _decode(cursor)
        query = query.where(
            or_(
                Notification.created_at < stamp,
                and_(Notification.created_at == stamp, Notification.id < rid),
            )
        )
    order = (Notification.created_at.desc(), Notification.id.desc())
    found = list(await db.scalars(query.order_by(*order).limit(limit + 1)))
    has_more = len(found) > limit
    rows = found[:limit]
    return NotificationPage(
        items=await _views(db, authz, ctx, rows),
        next_cursor=_encode(rows[-1]) if has_more else None,
        has_more=has_more,
    )


async def unread_count(db: AsyncSession, ctx: OrgContext) -> UnreadCount:
    unread = Notification.read_at.is_(None)
    total = await db.scalar(select(func.count()).where(await _scope(db, ctx), unread))
    member_orgs = select(OrganizationMember.org_id).where(
        OrganizationMember.user_id == ctx.user_id,
        OrganizationMember.tenant_id == ctx.tenant_id,
        OrganizationMember.status == "active",
    )
    per_org = await db.execute(
        select(Notification.org_id, func.count())
        .where(
            Notification.user_id == ctx.user_id,
            Notification.tenant_id == ctx.tenant_id,
            Notification.org_id.in_(member_orgs),
            Notification.created_at >= now() - RETENTION,
            unread,
        )
        .group_by(Notification.org_id)
    )
    return UnreadCount(
        total=total or 0, by_org={str(org): count for org, count in per_org.tuples().all()}
    )


async def _own(db: AsyncSession, ctx: OrgContext, notification_id: UUID) -> Notification:
    row = await db.scalar(
        select(Notification).where(Notification.id == notification_id, await _scope(db, ctx))
    )
    if row is None:
        raise denials.not_found()
    return row


async def mark_read(db: AsyncSession, ctx: OrgContext, notification_id: UUID) -> None:
    row = await _own(db, ctx, notification_id)
    if row.read_at is None:
        row.read_at = now()


async def mark_all_read(db: AsyncSession, ctx: OrgContext) -> int:
    result = await db.execute(
        update(Notification)
        .where(await _scope(db, ctx), Notification.read_at.is_(None))
        .values(read_at=now())
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def delete_notification(db: AsyncSession, ctx: OrgContext, notification_id: UUID) -> None:
    await db.delete(await _own(db, ctx, notification_id))
