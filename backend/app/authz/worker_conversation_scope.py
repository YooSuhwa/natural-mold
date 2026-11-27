"""Worker checkpoint consumers retain the captured private conversation actor."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_scope import lock_private_conversation
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.database import TenantScoped
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


def goal_reservation_owner_predicate(scope: TenantScoped) -> ColumnElement[bool]:
    """After revocation, a worker may stop reservations for its original owner and tenant."""
    return and_(Conversation.tenant_id == scope.tenant_id, Conversation.user_id == scope.user_id)
