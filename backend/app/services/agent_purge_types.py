"""Typed values shared by the Agent purge transaction phases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser


@dataclass(frozen=True, slots=True)
class AgentPurgeCommand:
    db: AsyncSession
    actor: CurrentUser
    agent_id: UUID
    confirmation_name: str
    checkpointer: BaseCheckpointSaver[str] | None = None


@dataclass(frozen=True, slots=True)
class PreparedAgentPurge:
    receipt_id: UUID
    tenant_id: UUID
    org_id: UUID
    owner_user_id: UUID
    agent_name: str
    conversation_ids: tuple[UUID, ...]
    database_deleted: bool


__all__ = ["AgentPurgeCommand", "PreparedAgentPurge"]
