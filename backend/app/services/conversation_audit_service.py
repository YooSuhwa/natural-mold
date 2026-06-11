from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.conversation_run import ConversationRun
from app.models.user import User
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


async def record_conversation_run_audit(
    db: AsyncSession,
    *,
    action: str,
    run: ConversationRun,
    user: CurrentUser | None = None,
    request: Request | None = None,
    status: str | None = None,
) -> None:
    if user is None:
        row = await db.execute(select(User).where(User.id == run.user_id))
        owner = row.scalar_one_or_none()
        actor_email = owner.email if owner is not None else None
        actor_name = owner.name if owner is not None else None
    else:
        actor_email = user.email
        actor_name = user.name

    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=run.user_id,
        actor_email_snapshot=actor_email,
        actor_label=actor_name,
        owner_user_id=run.user_id,
        owner_email_snapshot=actor_email,
        action=action,
        target_type="conversation",
        target_id=run.conversation_id,
        target_owner_user_id=run.user_id,
        outcome="success",
        request=request,
        run_id=run.id,
        metadata={
            "run_id": str(run.id),
            "conversation_id": str(run.conversation_id),
            "agent_id": str(run.agent_id),
            "source": run.source,
            "status": status or run.status,
        },
    )
