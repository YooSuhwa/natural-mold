"""Notification API (plan 9.7 알림, 14.2, S25; H-02). Every route is ``SELF``: the caller's
own rows in the current organization (plus company-level rows for company admins).

* ``GET /api/notifications?cursor=&limit=&unread=``: newest first; targets re-checked.
* ``GET /api/notifications/unread-count``: ``{"total": n, "by_org": {"<org_id>": n}}``.
* ``POST /api/notifications/{id}/read``, ``POST /api/notifications/read-all``.
* ``DELETE /api/notifications/{id}``.

Another user's or another organization's row answers 404.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notification_queries import (
    delete_notification,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)
from app.authz.org_context import OrgContext
from app.authz.policies import SELF, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.notification import NotificationPage, ReadAllResult, UnreadCount

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
@authz_policy(SELF)
async def get_notifications(
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    unread: bool = False,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> NotificationPage:
    return await list_notifications(db, authz, context, cursor=cursor, limit=limit, unread=unread)


@router.get("/unread-count", response_model=UnreadCount)
@authz_policy(SELF)
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> UnreadCount:
    return await unread_count(db, context)


@router.post("/read-all", response_model=ReadAllResult)
@authz_policy(SELF)
async def read_all_notifications(
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> ReadAllResult:
    updated = await mark_all_read(db, context)
    await db.commit()
    return ReadAllResult(updated=updated)


@router.post("/{notification_id}/read", status_code=204)
@authz_policy(SELF)
async def read_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> Response:
    await mark_read(db, context, notification_id)
    await db.commit()
    return Response(status_code=204)


@router.delete("/{notification_id}", status_code=204)
@authz_policy(SELF)
async def remove_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> Response:
    await delete_notification(db, context, notification_id)
    await db.commit()
    return Response(status_code=204)
