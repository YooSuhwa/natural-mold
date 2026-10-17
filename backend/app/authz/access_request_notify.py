"""Notification call sites of access requests (plan 14.2; delivered by H-02).

``access_request.created`` goes to the resource's managers (``can_share``) and
``access_request.decided`` to the requester. H-02 adds the ``notifications`` table and
replaces these no-ops; they run inside the request's transaction, after the row change.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_request import AccessRequest

CREATED_KIND = "access_request.created"
DECIDED_KIND = "access_request.decided"


async def notify_created(db: AsyncSession, request: AccessRequest) -> None:
    """H-02: notify every principal holding ``can_share`` on the requested resource."""
    del db, request


async def notify_decided(db: AsyncSession, request: AccessRequest) -> None:
    """H-02: notify the requester of an approval or a denial."""
    del db, request
