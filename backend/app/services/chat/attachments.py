"""Attachment linking, unified file listing, and attachment GC.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.schemas.conversation import FileItem
from app.services.artifact_service import list_conversation_artifacts

logger = logging.getLogger(__name__)


def _unlink_paths(paths: Sequence[str]) -> None:
    """Best-effort delete of stored upload files (runs off the event loop)."""

    for raw in paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            logger.warning("orphan attachment file delete failed: %s", raw, exc_info=True)


async def gc_orphan_attachments(db: AsyncSession, *, retention_hours: int) -> int:
    """Delete never-sent uploads (orphan ``message_attachments``) past the cutoff.

    ``POST /api/uploads`` creates a row with ``message_id IS NULL``; it is
    stamped with the user's message id at turn finalize (M1). A row whose
    ``message_id`` is still NULL after ``retention_hours`` was uploaded but
    never sent (composer abandoned) — invisible to every read path and never
    cleaned up, so both the DB row and its on-disk blob accumulate.

    Removes rows that are **both** ``message_id IS NULL`` AND older than the
    cutoff. The on-disk file is unlinked first (best-effort, off the event
    loop) so a delete failure can't strand bytes after the row is gone.
    Commits so the cron caller doesn't manage a transaction. Returns the
    number of orphan uploads deleted.
    """

    # Reject (not clamp) a non-positive retention — ``0`` sets ``cutoff = now``
    # and would reap an upload the user just staged but hasn't sent yet. A
    # mis-set value must surface as a config error, not silently destroy data.
    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    result = await db.execute(
        select(MessageAttachment).where(
            MessageAttachment.message_id.is_(None),
            MessageAttachment.created_at < cutoff,
        )
    )
    orphans = list(result.scalars().all())
    if not orphans:
        return 0

    await asyncio.to_thread(_unlink_paths, [att.storage_path for att in orphans])

    ids = [att.id for att in orphans]
    await db.execute(delete(MessageAttachment).where(MessageAttachment.id.in_(ids)))
    await db.commit()
    logger.info(
        "Orphan attachment GC: deleted %d never-sent upload(s) older than %s",
        len(ids),
        cutoff.isoformat(),
    )
    return len(ids)


async def link_attachments_to_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    attachment_ids: list[uuid.UUID],
) -> None:
    """Stamp orphan ``MessageAttachment`` rows with their conversation id.

    ``message_id`` stays null at send time — LangGraph assigns the user
    HumanMessage id only inside the run. It is backfilled at turn finalize by
    :func:`link_attachments_to_message` (M1) so reads can echo the attachment on
    the right user bubble.
    """

    if not attachment_ids:
        return
    await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.id.in_(attachment_ids),
            MessageAttachment.user_id == user_id,
        )
        .values(conversation_id=conversation_id)
    )
    await db.flush()


async def resolve_turn_user_message_id(
    db: AsyncSession,
    conversation: Conversation,
    *,
    tree: Any = None,
) -> str | None:
    """Resolve THIS turn's user message id as the read path will compute it (M1).

    A sent upload's ``message_attachments.message_id`` must equal the id that
    :func:`list_messages_from_checkpointer` will later key attachment hydration
    on — i.e. ``str(parse_msg_id(msg.id, conversation.id, idx))`` for the user
    message. We reproduce **the exact same tree walk and enumeration** that the
    read path uses (``messages = [node.message for node in tree.nodes]`` →
    ``enumerate``), then take the **last** ``human`` message: the assistant's
    reply never appends a HumanMessage, so the last human in the active chain is
    always the message the user just sent. This holds across multi-turn /
    branch / HiTL-interrupt because the active chain is rebuilt each time.

    ``msg_id_sink`` carries **AI** message ids only (streaming only sinks
    ``ai``/``AIMessageChunk``), so the user id is never available there — it
    must be derived from the post-run checkpoint, never assumed at ``idx=0``.

    Returns the id as a string, or ``None`` if there is no user message (e.g.
    an empty/garbage checkpoint). ``db`` is unused today but kept in the
    signature so a future pricing/identity lookup needn't change call sites.
    """

    if tree is None:
        from app.agent_runtime.checkpointer import get_checkpointer
        from app.services.thread_branch_service import build_message_tree

        tree = await build_message_tree(
            get_checkpointer(),
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )

    if not tree.nodes:
        return None

    messages = [node.message for node in tree.nodes]
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if getattr(msg, "type", None) == "human":
            return str(parse_msg_id(getattr(msg, "id", None), conversation.id, idx))
    return None


async def link_attachments_to_message(
    db: AsyncSession,
    *,
    attachment_ids: list[uuid.UUID],
    message_id: str,
) -> int:
    """Backfill ``message_attachments.message_id`` for this send's uploads (M1).

    Only rows whose ``message_id`` is still NULL are stamped, so a stale orphan
    from an earlier turn whose finalize failed can't be mis-attached to this
    turn's user message (cross-send mis-link guard). Returns the rows updated.
    """

    if not attachment_ids:
        return 0
    result = await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.id.in_(attachment_ids),
            MessageAttachment.message_id.is_(None),
        )
        .values(message_id=message_id)
    )
    await db.flush()
    return int(getattr(result, "rowcount", 0) or 0)


async def list_conversation_files(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[FileItem]:
    """Unified conversation file list (D12/M3): generated + attached, merged.

    Returns one created_at-sorted (newest first) stream of :class:`FileItem`,
    each tagged ``source``. Generated files reuse the artifact summaries
    (editable); attachments are the sent uploads (``message_id`` backfilled),
    read-only with preview/download pointing at ``/api/uploads/{id}``. The
    caller owns the ownership guard; both queries are already scoped to the
    conversation (and artifacts additionally to ``user_id``).
    """

    artifacts = await list_conversation_artifacts(
        db, user_id=user_id, conversation_id=conversation_id
    )
    items: list[FileItem] = [
        FileItem(
            source="generated",
            id=str(a.id),
            name=a.display_name,
            mime_type=a.mime_type,
            extension=a.extension,
            kind=a.artifact_kind,
            size_bytes=a.size_bytes,
            preview_url=a.preview_url,
            download_url=a.download_url,
            # Prefer the real assistant message id (parse_msg_id form, matches the
            # bubble anchor) so "jump to message" lands on the right turn;
            # ``assistant_msg_id`` is the run id and won't match the anchor.
            message_id=(a.linked_message_ids[0] if a.linked_message_ids else a.assistant_msg_id),
            created_at=a.created_at,
            editable=True,
        )
        for a in artifacts
    ]

    attach_rows = (
        (
            await db.execute(
                select(MessageAttachment).where(
                    MessageAttachment.conversation_id == conversation_id,
                    MessageAttachment.message_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    items.extend(
        FileItem(
            source="attached",
            id=str(att.id),
            name=att.filename,
            mime_type=att.mime_type,
            extension=(Path(att.filename).suffix.lstrip(".") or None),
            kind=None,
            size_bytes=att.size_bytes,
            preview_url=att.url,
            download_url=att.url,
            message_id=att.message_id,
            created_at=att.created_at,
            editable=False,
        )
        for att in attach_rows
    )

    items.sort(key=lambda f: f.created_at, reverse=True)
    return items
