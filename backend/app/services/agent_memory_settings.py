"""Persist agent memory settings under the owner's deletion fence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import AGENT_MEMORY_SETTINGS_UPDATE
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.models.agent import Agent
from app.models.memory import AgentMemorySettings
from app.schemas.memory import AgentMemorySettingsUpdate
from app.services import audit_service


async def _owned_settings(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> tuple[AgentMemorySettings | None, bool]:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    # Only a key-share lock is needed: it fences DELETE without taking any
    # conversation/run locks or blocking ordinary owner metadata updates.
    owner = await db.scalar(
        select(Agent.id)
        .where(Agent.id == agent_id, private_scope_predicate(Agent, context))
        .with_for_update(read=True, key_share=True)
    )
    if owner is None:
        return None, False
    settings = await db.scalar(
        select(AgentMemorySettings)
        .where(AgentMemorySettings.agent_id == agent_id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    if settings is not None:
        return settings, False
    settings = AgentMemorySettings(agent_id=agent_id)
    db.add(settings)
    return settings, True


async def _recover_settings(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> tuple[AgentMemorySettings | None, bool]:
    await db.rollback()
    settings, created = await _owned_settings(db, agent_id, context)
    if created:
        # An integrity error without a winning row is not a duplicate insert.
        await db.rollback()
    return settings, created


async def get_agent_settings(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> AgentMemorySettings | None:
    settings, created = await _owned_settings(db, agent_id, context)
    if settings is None or not created:
        return settings
    try:
        await db.commit()
    except IntegrityError:
        settings, missing_winner = await _recover_settings(db, agent_id, context)
        if missing_winner:
            raise
    # expire_on_commit=False retains the INSERT's defaults. A refresh after
    # commit would release the fence before querying a possibly deleted row.
    return settings


async def update_agent_settings(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
    payload: AgentMemorySettingsUpdate,
    *,
    actor: OrgContext | None = None,
) -> AgentMemorySettings | None:
    """``context`` is the owner's scope; ``actor`` the caller (F-09: a non-owner editor's
    change is audited as ``agent.memory_settings_update`` with the editor as actor)."""
    settings, _ = await _owned_settings(db, agent_id, context)
    if settings is None:
        return None
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(settings, field, value)
    settings.updated_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        await db.commit()
    except IntegrityError:
        settings, missing_winner = await _recover_settings(db, agent_id, context)
        if missing_winner:
            raise
        if settings is None:
            return None
        for field, value in changes.items():
            setattr(settings, field, value)
        settings.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
    if actor is not None and actor.user_id != context.user_id:
        await _audit_editor(db, actor, context, agent_id, sorted(changes))
    return settings


async def _audit_editor(
    db: AsyncSession, actor: OrgContext, owner: OrgContext, agent_id: uuid.UUID, fields: list
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.user_id,
        owner_user_id=owner.user_id,
        action=AGENT_MEMORY_SETTINGS_UPDATE,
        target_type="agent",
        target_id=agent_id,
        target_owner_user_id=owner.user_id,
        outcome="success",
        metadata={"changed_fields": fields},
    )
    await db.commit()
