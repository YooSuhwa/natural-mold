"""Persist agent memory settings under the owner's deletion fence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.memory import AgentMemorySettings
from app.schemas.memory import AgentMemorySettingsUpdate


async def _owned_settings(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[AgentMemorySettings | None, bool]:
    # Only a key-share lock is needed: it fences DELETE without taking any
    # conversation/run locks or blocking ordinary owner metadata updates.
    owner = await db.scalar(
        select(Agent.id)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .with_for_update(read=True, key_share=True)
    )
    if owner is None:
        return None, False
    settings = await db.scalar(
        select(AgentMemorySettings).where(AgentMemorySettings.agent_id == agent_id)
    )
    if settings is not None:
        return settings, False
    settings = AgentMemorySettings(agent_id=agent_id)
    db.add(settings)
    return settings, True


async def _recover_settings(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[AgentMemorySettings | None, bool]:
    await db.rollback()
    settings, created = await _owned_settings(db, agent_id, user_id)
    if created:
        # An integrity error without a winning row is not a duplicate insert.
        await db.rollback()
    return settings, created


async def get_agent_settings(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> AgentMemorySettings | None:
    settings, created = await _owned_settings(db, agent_id, user_id)
    if settings is None or not created:
        return settings
    try:
        await db.commit()
    except IntegrityError:
        settings, missing_winner = await _recover_settings(db, agent_id, user_id)
        if missing_winner:
            raise
    # expire_on_commit=False retains the INSERT's defaults. A refresh after
    # commit would release the fence before querying a possibly deleted row.
    return settings


async def update_agent_settings(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: AgentMemorySettingsUpdate,
) -> AgentMemorySettings | None:
    settings, _ = await _owned_settings(db, agent_id, user_id)
    if settings is None:
        return None
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(settings, field, value)
    settings.updated_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        await db.commit()
    except IntegrityError:
        settings, missing_winner = await _recover_settings(db, agent_id, user_id)
        if missing_winner:
            raise
        if settings is None:
            return None
        for field, value in changes.items():
            setattr(settings, field, value)
        settings.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
    return settings
