"""Delete conversations for retention, then clean their files (plan 10.7).

The user delete path (``chat.conversations.delete_conversation``) re-resolves the owner
as a live actor; retention runs without one (a removed member is no longer an actor),
so this deletes by row id. Each row is locked and the full selection is evaluated again
under that lock, so a conversation that became active, or whose member was re-added,
after the candidate list was read is kept. A conversation with an active run is
skipped and picked up by the next sweep.

The row is deleted and committed first (child rows cascade); checkpoints, internal
offloads, attachment blobs and the local artifact tree are removed afterwards, best
effort: a failure is logged and leaves an orphan for the existing GC jobs. Not covered:
uploads never linked to the conversation (orphan-attachment GC owns them) and artifact
objects of a non-local storage backend.
"""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.agent_runtime.checkpointer import delete_thread
from app.agent_runtime.offload_storage import delete_conversation_offloads
from app.agent_runtime.runtime_config import runtime_data_dir
from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment

logger = logging.getLogger(__name__)
PreLock = Callable[[AsyncSession, UUID], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DeletedConversation:
    conversation_id: UUID
    owner_id: UUID
    attachment_paths: tuple[str, ...]


def _remove_files(paths: Sequence[str], artifact_root: Path | None) -> None:
    for raw in paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            logger.warning("retention attachment delete failed: %s", raw, exc_info=True)
    if artifact_root is not None:
        shutil.rmtree(artifact_root, ignore_errors=True)


def _artifact_root(conversation_id: UUID) -> Path | None:
    if settings.artifact_storage_backend != "local":
        return None
    root = Path(settings.artifact_storage_dir).resolve()
    target = (root / "conversations" / str(conversation_id)).resolve()
    return target if target.is_relative_to(root) and target.is_dir() else None


async def delete_conversation_row(
    db: AsyncSession, conversation_id: UUID, matching: Select[tuple[UUID]]
) -> DeletedConversation | None:
    """Delete one row if ``matching`` (a ``Conversation.id`` selection) still selects it."""
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        return None
    if await db.scalar(matching.where(Conversation.id == conversation_id)) is None:
        return None
    active = await db.scalar(
        select(ConversationRun.id)
        .where(
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.is_active.is_(True),
        )
        .limit(1)
    )
    if active is not None:
        return None
    paths = tuple(
        await db.scalars(
            select(MessageAttachment.storage_path).where(
                MessageAttachment.conversation_id == conversation_id
            )
        )
    )
    await db.execute(
        update(Conversation)
        .where(
            Conversation.side_chat_parent_id == conversation_id,
            Conversation.source == "side_chat",
        )
        .values(source="ui", side_chat_parent_id=None)
    )
    for child in (MessageAttachment, ConversationArtifact):
        await db.execute(delete(child).where(child.conversation_id == conversation_id))
    deleted = DeletedConversation(conversation_id, conversation.user_id, paths)
    await db.delete(conversation)
    await db.flush()
    return deleted


async def clean_up_files(deleted: DeletedConversation) -> None:
    """After the commit; each step is independent and failures are only logged."""
    conversation = str(deleted.conversation_id)
    try:
        await delete_thread(conversation)
    except Exception:  # noqa: BLE001 - orphaned checkpoints are harmless
        logger.warning("retention checkpoint delete failed: %s", conversation, exc_info=True)
    try:
        delete_conversation_offloads(
            runtime_data_dir(), owner_id=str(deleted.owner_id), conversation_id=conversation
        )
    except Exception:  # noqa: BLE001
        logger.warning("retention offload delete failed: %s", conversation, exc_info=True)
    try:
        root = _artifact_root(deleted.conversation_id)
        await asyncio.to_thread(_remove_files, deleted.attachment_paths, root)
    except Exception:  # noqa: BLE001
        logger.warning("retention file delete failed: %s", conversation, exc_info=True)


async def delete_conversations(
    db: AsyncSession,
    conversation_ids: Sequence[UUID],
    matching: Select[tuple[UUID]],
    prelock: PreLock | None = None,
) -> int:
    """Each id in its own transaction; ``prelock`` takes locks that precede the row's."""
    deleted = 0
    for conversation_id in conversation_ids:
        try:
            if prelock is not None:
                await prelock(db, conversation_id)
            removed = await delete_conversation_row(db, conversation_id, matching)
            await db.commit()
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            await db.rollback()
            logger.exception("retention conversation delete failed: %s", conversation_id)
            continue
        if removed is not None:
            deleted += 1
            await clean_up_files(removed)
    return deleted
