"""Company of a public share token: rate-limit bucket and session scope (I-08, I-06)."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import platform_scope
from app.dependencies import get_db
from app.services import share_service
from app.tenancy.session_binding import bind_company_once


async def public_share_rate_scope(
    request: Request, share_token: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Record the token's company for the ``(tenant_id, ip)`` bucket of ``share_key``.

    Runs before the limiter check (slowapi checks after dependency resolution) and
    never raises, so unknown tokens are throttled too (their bucket is ``unknown``).
    I-06: the token, looked up in platform scope, also binds the anonymous request
    session to the link's company (as the conversation owner) before any read; an
    unknown token leaves it unbound (0 rows under RLS).
    """
    async with platform_scope(db, "share.public_token"):
        owner = await share_service.share_token_owner(db, share_token)
    request.state.share_tenant_id = None if owner is None else owner[0]
    if owner is not None:
        await bind_company_once(db, *owner)
