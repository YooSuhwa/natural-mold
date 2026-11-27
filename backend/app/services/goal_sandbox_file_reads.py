"""Record the owner's file transfer as activity on their goal workspaces.

The bytes always come from Moldy's canonical file store. Anonymous readers of
published copies cannot change an organization's sandbox lifetime.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import anyio
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.services import goal_sandbox_leases as leases

logger = logging.getLogger(__name__)
_READ_TTL = timedelta(seconds=90)
_RENEW_SECONDS = 30.0


async def _renew(context: OrgContext, conversation_id: UUID, tokens: tuple[UUID, ...]) -> None:
    while True:
        await asyncio.sleep(_RENEW_SECONDS)
        try:
            async with tenant_session(context) as db:
                active = await leases.renew_conversation_reads(
                    db,
                    tenant_id=context.tenant_id,
                    org_id=context.org_id,
                    user_id=context.user_id,
                    conversation_id=conversation_id,
                    tokens=tokens,
                    ttl=_READ_TTL,
                )
                await db.commit()
            if active == 0:
                return
        except Exception:
            # The transfer is from the canonical copy. A maintenance DB outage
            # must not interrupt a working download or start a remote machine.
            logger.warning("Could not renew goal sandbox file activity", exc_info=True)


@asynccontextmanager
async def hold_goal_sandbox_reads(
    context: OrgContext, conversation_id: UUID
) -> AsyncIterator[bool]:
    """Hold renewable leases only for existing workspaces of this owner and conversation."""
    if not settings.goal_sandbox_enabled:
        yield False
        return

    try:
        async with tenant_session(context) as db:
            tokens = await leases.acquire_conversation_reads(
                db,
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id=conversation_id,
                ttl=_READ_TTL,
            )
            await db.commit()
    except Exception:
        logger.warning("Could not register goal sandbox file activity", exc_info=True)
        tokens = ()

    if not tokens:
        yield False
        return

    heartbeat = asyncio.create_task(_renew(context, conversation_id, tokens))
    try:
        yield True
    finally:
        heartbeat.cancel()
        with anyio.move_on_after(5, shield=True):
            with suppress(asyncio.CancelledError):
                await heartbeat
            try:
                async with tenant_session(context) as db:
                    await leases.release_conversation_reads(
                        db,
                        tenant_id=context.tenant_id,
                        org_id=context.org_id,
                        user_id=context.user_id,
                        conversation_id=conversation_id,
                        tokens=tokens,
                    )
                    await db.commit()
            except Exception:
                # Each token has a short DB expiry even if the serving worker exits.
                logger.warning("Could not release goal sandbox file activity", exc_info=True)


class ConversationFileResponse(FileResponse):
    """Keep activity across range requests, backpressure, and disconnects."""

    def __init__(
        self,
        path: Path,
        *,
        context: OrgContext,
        conversation_id: UUID,
        media_type: str | None,
        headers: Mapping[str, str],
        filename: str | None = None,
    ) -> None:
        super().__init__(path, media_type=media_type, headers=headers, filename=filename)
        self.context = context
        self.conversation_id = conversation_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with hold_goal_sandbox_reads(self.context, self.conversation_id) as holding:
            if holding and "http.response.pathsend" in scope.get("extensions", {}):
                # Delegate to Starlette's normal chunked send: a server-side path
                # handoff can outlive the response object and its read lease.
                extensions = dict(scope["extensions"])
                del extensions["http.response.pathsend"]
                scope = {**scope, "extensions": extensions}
            await super().__call__(scope, receive, send)
