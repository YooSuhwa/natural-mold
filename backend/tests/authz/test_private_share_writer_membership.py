"""Private share writers must revalidate captured authority before durable effects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.share_link import ShareLink
from app.services import share_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def _suspend_membership(db: AsyncSession, scopes: PrivateScopes) -> None:
    own = scopes.first
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    status = await db.scalar(
        select(OrganizationMember.status).where(
            OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
        )
    )
    assert status == "suspended"


async def test_share_creation_revalidates_captured_membership(
    db: AsyncSession, private_scopes: PrivateScopes, collector_case: CollectorCase
) -> None:
    own = private_scopes.first
    conversation = collector_case.conversation
    # Owned creation/revocation is a real control before the membership change.
    link = await share_service.create_or_get_active_share(db, conversation, own)
    assert link.org_id == own.org_id
    assert await share_service.revoke_share(db, conversation.id, private_scopes.second) == []
    assert await share_service.revoke_share(db, conversation.id, own)
    before = await db.scalar(select(func.count()).select_from(ShareLink))
    await _suspend_membership(db, private_scopes)
    denied = False
    try:
        await share_service.create_or_get_active_share(db, conversation, own)
    except AppError:
        denied = True
    after = await db.scalar(select(func.count()).select_from(ShareLink))
    assert denied, (
        f"Captured suspended membership accepted by create: denied={denied}, "
        f"share_rows_before={before}, share_rows_after={after}"
    )
    assert after == before


async def test_share_revocation_revalidates_captured_membership(
    db: AsyncSession, private_scopes: PrivateScopes, collector_case: CollectorCase
) -> None:
    own = private_scopes.first
    conversation = collector_case.conversation
    link = await share_service.create_or_get_active_share(db, conversation, own)
    assert link.revoked_at is None
    await _suspend_membership(db, private_scopes)
    denied = False
    try:
        await share_service.revoke_share(db, conversation.id, own)
    except AppError:
        denied = True
    persisted = await db.scalar(select(ShareLink.revoked_at).where(ShareLink.id == link.id))
    assert denied, (
        f"Captured suspended membership accepted by revoke: denied={denied}, "
        f"persisted_revocation={persisted is not None}"
    )
    assert persisted is None
