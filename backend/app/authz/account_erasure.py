"""Global account erasure guards; no private content or organization is inferred."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_member_exit import owner_queries
from app.models.conversation import Conversation


async def require_account_resources_released(db: AsyncSession, user_id: UUID) -> None:
    """The caller holds the User deletion lock before checking all restricted owner FKs."""
    for query in owner_queries(user_id):
        if await db.scalar(query.limit(1)) is not None:
            raise authorization_error("resource_transfer_required")


async def lock_account_history(db: AsyncSession, user_id: UUID) -> list[UUID]:
    """Account cleanup uses direct conversation ownership across the account's scopes."""
    return list(
        (
            await db.scalars(
                select(Conversation.id)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.id)
                .with_for_update(of=Conversation)
            )
        ).all()
    )
