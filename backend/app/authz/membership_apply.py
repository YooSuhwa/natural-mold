"""Router side of membership tuple sync: apply the queued rows after the commit (8.6 item 2)."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.membership_sync import apply_membership_outbox
from app.authz.outbox import OutboxRun
from app.authz.runtime import get_authz


async def apply_for_request(request: Request, db: AsyncSession) -> OutboxRun:
    """Best effort, never raises; the C-05 drain retries whatever is left."""
    return await apply_membership_outbox(db, await get_authz(request))
