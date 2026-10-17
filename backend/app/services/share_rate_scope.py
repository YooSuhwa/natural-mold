"""Company of a public share token for its rate-limit bucket (I-08, plan 8.9)."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.services import share_service


async def public_share_rate_scope(
    request: Request, share_token: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Record the token's company for the ``(tenant_id, ip)`` bucket of ``share_key``.

    Runs before the limiter check (slowapi checks after dependency resolution) and
    never raises, so unknown tokens are throttled too (their bucket is ``unknown``).
    """
    request.state.share_tenant_id = await share_service.share_token_tenant(db, share_token)
