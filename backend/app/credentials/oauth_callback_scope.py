"""I-06: the OAuth callback's state row picks the request session's company.

The callback is a public route without an org context. The live state row and
its credential's company are read in one platform-scope query; only when the
state is valid (known, unconsumed, unexpired) is the session bound to that
company as the user who started the flow. An unknown state leaves it unbound.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import platform_scope
from app.models.credential import Credential
from app.models.credential_oauth_state import CredentialOAuthState
from app.tenancy.session_binding import bind_company_once


async def live_callback_state(
    db: AsyncSession, state_hash: str, now: datetime
) -> CredentialOAuthState | None:
    async with platform_scope(db, "oauth.callback_state"):
        found = (
            await db.execute(
                select(CredentialOAuthState, Credential.tenant_id)
                .outerjoin(Credential, Credential.id == CredentialOAuthState.credential_id)
                .where(
                    CredentialOAuthState.state_hash == state_hash,
                    CredentialOAuthState.consumed_at.is_(None),
                    CredentialOAuthState.expires_at > now,
                )
                .with_for_update(of=CredentialOAuthState)
            )
        ).one_or_none()
    pending = None if found is None else found[0]
    tenant_id = None if found is None else found[1]
    if pending is not None and tenant_id is not None:
        await bind_company_once(db, tenant_id, pending.user_id)
    return pending
