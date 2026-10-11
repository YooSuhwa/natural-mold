"""Worker checkpoint consumers retain the captured private conversation actor."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import lock_private_conversation
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.exceptions import AppError
from app.models.conversation import Conversation


@dataclass(frozen=True, slots=True)
class WorkerConversationTarget:
    conversation_id: UUID
    context: OrgContext


async def load_worker_conversation(
    db: AsyncSession, target: WorkerConversationTarget
) -> Conversation | None:
    """Lock the original scoped parent and verify its current actor before private work."""
    try:
        await require_private_actor(db, target.context)
        return await lock_private_conversation(db, target.conversation_id, target.context)
    except AppError:
        return None
