"""Transaction-B relational detach/delete for Agent purge."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import delete, select, update

from app.agent_runtime.runtime_config import runtime_data_dir
from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthorization,
    authorize_agent_lifecycle,
    record_agent_lifecycle_success,
)
from app.authz.error_codes import agent_lifecycle_execution_pending, agent_not_archived
from app.models.agent import Agent
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.conversation import Conversation
from app.models.share_link import ShareLink
from app.services.agent_image_paths import find_agent_avatar_path
from app.services.agent_lifecycle_projection import lifecycle_state, lock_lifecycle_target
from app.services.agent_purge_retained import detach_retained_agent_references
from app.services.agent_purge_types import AgentPurgeCommand, PreparedAgentPurge
from app.services.conversation_history_snapshot import require_complete_agent_history


async def finalize_agent_purge(
    command: AgentPurgeCommand,
    prepared: PreparedAgentPurge,
) -> None:
    """Revalidate every gate, detach retained history, and stage cleanup items."""
    db = command.db
    await lock_lifecycle_target(db, command.agent_id)
    authority = await authorize_agent_lifecycle(
        db,
        command.actor,
        AgentLifecycleAuthorization(command.agent_id, "purge", command.confirmation_name),
    )
    if authority.archived_at is None:
        raise agent_not_archived()
    if await lifecycle_state(db, authority) != "archived_stopped":
        raise agent_lifecycle_execution_pending()
    agent_id = await db.scalar(
        select(Agent.id)
        .where(
            Agent.id == command.agent_id,
            Agent.tenant_id == prepared.tenant_id,
            Agent.org_id == prepared.org_id,
            Agent.user_id == prepared.owner_user_id,
        )
        .with_for_update()
    )
    receipt = await db.scalar(
        select(AgentPurgeReceipt)
        .where(
            AgentPurgeReceipt.id == prepared.receipt_id,
            AgentPurgeReceipt.state == "preparing",
            AgentPurgeReceipt.agent_id_snapshot == command.agent_id,
            AgentPurgeReceipt.agent_name_snapshot == command.confirmation_name,
        )
        .with_for_update()
    )
    if agent_id is None or receipt is None:
        raise agent_lifecycle_execution_pending()
    conversation_ids = tuple(
        await db.scalars(
            select(Conversation.id)
            .where(Conversation.agent_id == command.agent_id)
            .order_by(Conversation.id)
            .with_for_update(of=Conversation)
        )
    )
    if conversation_ids != prepared.conversation_ids:
        raise agent_lifecycle_execution_pending()
    await require_complete_agent_history(db, command.agent_id, conversation_ids)
    cleanup_targets: list[tuple[str, str]] = []
    avatar = find_agent_avatar_path(command.agent_id)
    if avatar is not None and not avatar.is_symlink():
        cleanup_targets.append(("agent_image", str(avatar.absolute())))
    files_root = (runtime_data_dir() / "agents" / str(command.agent_id)).absolute()
    cleanup_targets.append(("agent_files", str(files_root)))
    cleanup_targets.extend(("raw_checkpoint", str(item)) for item in conversation_ids)
    db.add_all(
        [
            AgentPurgeCleanupItem(
                receipt_id=receipt.id,
                kind=kind,
                locator=locator,
                locator_sha256=sha256(locator.encode()).hexdigest(),
            )
            for kind, locator in cleanup_targets
        ]
    )
    purged_at = datetime.now(UTC)
    if conversation_ids:
        await db.execute(
            update(Conversation)
            .where(Conversation.id.in_(conversation_ids))
            .values(
                agent_id=None,
                agent_lifecycle_state="purged",
                agent_purged_at=purged_at,
                queue_paused=True,
                queue_paused_at=purged_at.replace(tzinfo=None),
            )
        )
        await db.execute(delete(ShareLink).where(ShareLink.conversation_id.in_(conversation_ids)))
    await detach_retained_agent_references(db, command.agent_id)
    await record_agent_lifecycle_success(db, authority, "purge")
    await db.execute(delete(Agent).where(Agent.id == command.agent_id))
    receipt.state = "db_deleted"
    receipt.failure_code = None
    receipt.failure_message = None
    await db.flush()


__all__ = ["finalize_agent_purge"]
