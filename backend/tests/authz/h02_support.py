"""Seeds and helpers for the H-02 notification tests (plan 14.2, S25)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import Target, notify
from app.models.notification import Notification
from app.models.organization import OrganizationMember
from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

PATH = "/api/notifications"


async def push(
    db: AsyncSession,
    *,
    user: UUID = TEST_USER_ID,
    org: UUID | None = TEST_ORG_ID,
    kind: str = "grant.received",
    target: Target | None = None,
    actor: UUID | None = None,
    link: str | None = "/tools",
    **payload: str | None,
) -> Notification:
    [row] = await notify(
        db,
        kind=kind,
        recipients=[user],
        tenant_id=TEST_TENANT_ID,
        org_id=org,
        target=target,
        actor_id=actor,
        payload=payload,
        link=link,
    )
    await db.commit()
    return row


async def join_other_org(db: AsyncSession, user: UUID = TEST_USER_ID) -> None:
    db.add(OrganizationMember(org_id=OTHER_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=user))
    await db.commit()


async def backdate(row_id: UUID, stamp: datetime) -> None:
    async with TestSession() as db:
        await db.execute(
            update(Notification).where(Notification.id == row_id).values(created_at=stamp)
        )
        await db.commit()


async def rows(user: UUID | None = None, kind: str | None = None) -> list[Notification]:
    async with TestSession() as db:
        query = select(Notification).order_by(Notification.created_at, Notification.id)
        if user is not None:
            query = query.where(Notification.user_id == user)
        if kind is not None:
            query = query.where(Notification.kind == kind)
        return list(await db.scalars(query))
