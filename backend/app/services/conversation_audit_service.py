from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.services import audit_service


async def record_conversation_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="conversation",
        target_id=conversation_id,
        target_name_snapshot=title,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "agent_id": str(agent_id) if agent_id else None,
            **(metadata or {}),
        },
    )
