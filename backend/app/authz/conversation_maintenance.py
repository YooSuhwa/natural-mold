"""Explicit system maintenance locks for stopping persisted conversation work."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.authz.platform_scope import PlatformScope
from app.models.conversation import Conversation


@dataclass(frozen=True, slots=True)
class ConversationMaintenanceTarget:
    conversation_id: UUID
    owner_id: UUID
    authority: PlatformScope


async def lock_maintenance_conversation(
    db: AsyncSession, target: ConversationMaintenanceTarget
) -> Conversation | None:
    """Cancellation may stop work after actor revocation; it cannot create or execute work."""
    return await db.scalar(
        select(Conversation)
        .options(
            load_only(
                Conversation.id,
                Conversation.user_id,
                Conversation.org_id,
                Conversation.tenant_id,
                raiseload=True,
            )
        )
        .where(
            Conversation.id == target.conversation_id,
            Conversation.user_id == target.owner_id,
        )
        .with_for_update(of=Conversation)
        .execution_options(populate_existing=True, autoflush=False)
    )
