"""Read the selected root branch from a purged conversation's retained manifest."""

from __future__ import annotations

from collections.abc import Collection
from itertools import batched
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryManifest,
)

if TYPE_CHECKING:
    from app.services.ag_ui_snapshot_history import SnapshotHistory


async def read_retained_snapshot_history(
    db: AsyncSession,
    conversation: Conversation,
    *,
    secret_values: Collection[str] = (),
) -> SnapshotHistory:
    """Called after the private conversation gate. Never opens a checkpointer."""
    from app.services.ag_ui_snapshot_history import (
        SnapshotHistory,
        SnapshotMessageRef,
        _conflict,
        _replace_snapshot,
        _select_lineage,
        _snapshot_todos,
    )

    manifest_id = await db.scalar(
        select(ConversationHistoryManifest.id).where(
            ConversationHistoryManifest.conversation_id == conversation.id,
            ConversationHistoryManifest.schema_version == 1,
            ConversationHistoryManifest.state == "complete",
        ),
        execution_options={"autoflush": False},
    )
    if manifest_id is None:
        raise ConflictError(
            "CONVERSATION_HISTORY_INCOMPLETE",
            "Purged conversation has no complete retained history manifest",
        )

    checkpoint = ConversationHistoryCheckpoint
    root_scope = (
        checkpoint.manifest_id == manifest_id,
        checkpoint.conversation_id == conversation.id,
        checkpoint.checkpoint_namespace == "",
    )
    # Retained checkpoints are sorted by LangGraph checkpoint ID at materialization.
    headers = await db.execute(
        select(checkpoint.checkpoint_id, checkpoint.parent_checkpoint_id, checkpoint.branch)
        .where(*root_scope)
        .order_by(checkpoint.checkpoint_id.desc()),
        execution_options={"autoflush": False},
    )
    parents: dict[str, str | None] = {}
    active: list[str] = []
    for checkpoint_id, parent_id, branch in headers.tuples():
        parents[checkpoint_id] = parent_id
        if not isinstance(branch, dict):
            raise _conflict()
        if branch.get("active") is True:
            active.append(checkpoint_id)

    selected = conversation.active_branch_checkpoint_id
    if selected is None and active:
        if len(active) > 1:
            raise _conflict()
        selected = active[0]
    lineage = _select_lineage(parents, selected)
    refs: list[SnapshotMessageRef] = []
    # Each retained checkpoint contains a materialized transcript. Bound the number
    # held at once and skip the trace, channel_values, and inactive branches.
    for ids in batched(lineage, 16):
        rows = await db.execute(
            select(checkpoint.checkpoint_id, checkpoint.messages).where(
                *root_scope, checkpoint.checkpoint_id.in_(ids)
            ),
            execution_options={"autoflush": False},
        )
        page = dict(rows.tuples().all())
        for checkpoint_id in ids:
            messages = page.get(checkpoint_id)
            if not isinstance(messages, list):
                raise _conflict()
            typed = [message for message in messages if isinstance(message, dict)]
            if len(typed) != len(messages):
                raise _conflict()
            refs = _replace_snapshot(refs, typed, checkpoint_id, parents[checkpoint_id])
    leaf = lineage[-1] if lineage else None
    todos = ()
    if leaf is not None:
        row = (
            await db.execute(
                select(checkpoint.channel_values["todos"]).where(
                    *root_scope, checkpoint.checkpoint_id == leaf
                ),
                execution_options={"autoflush": False},
            )
        ).one_or_none()
        if row is None:
            raise _conflict()
        todos = _snapshot_todos(row[0], secret_values)

    return SnapshotHistory(
        checkpoint_id=leaf,
        messages=tuple(refs),
        source="retained",
        lineage_checkpoint_ids=lineage,
        has_branches=len(parents.keys() - set(parents.values())) > 1,
        todos=todos,
    )
