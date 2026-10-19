"""Narrow metadata projection helpers for Agent lifecycle governance."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_lifecycle_scope import AgentLifecycleAuthority, AgentLifecycleState
from app.schemas.agent_lifecycle import AgentLifecycleMetadataResponse
from app.services.agent_execution_owner import active_unlinked_envelopes, participant_run_conflicts
from app.services.agent_lifecycle_locks import lock_agent_tree


async def lock_lifecycle_target(db: AsyncSession, authority_id: UUID) -> None:
    """Acquire the full production advisory lock path for every lifecycle write."""
    await lock_agent_tree(db, authority_id)


async def lifecycle_state(
    db: AsyncSession, authority: AgentLifecycleAuthority
) -> AgentLifecycleState:
    if authority.archived_at is None:
        return "active"
    run_ids = await participant_run_conflicts(db, authority.agent_id)
    unlinked = await active_unlinked_envelopes(db, (authority.agent_id,))
    return "archived_stopping" if run_ids or unlinked.exists else "archived_stopped"


def lifecycle_metadata(
    authority: AgentLifecycleAuthority,
    state: AgentLifecycleState,
) -> AgentLifecycleMetadataResponse:
    return AgentLifecycleMetadataResponse(
        agent_id=authority.agent_id,
        name=authority.name,
        owner_user_id=authority.owner_user_id,
        owner_label=authority.owner_label,
        archived_by_user_id=authority.archived_by_user_id,
        archived_at=authority.archived_at,
        lifecycle_state=state,
        policy_delete_at=authority.policy_delete_at,
        can_archive=authority.can_archive,
        can_restore=authority.can_restore,
        can_purge=authority.can_purge,
    )


__all__ = ["lifecycle_metadata", "lifecycle_state", "lock_lifecycle_target"]
