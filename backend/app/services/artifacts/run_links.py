"""Per-run artifact link/finalize maintenance (split from ``recorder``, moved verbatim)."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_artifact import ConversationArtifact


async def link_artifacts_to_messages(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
    assistant_msg_id: str,
    linked_message_ids: list[str],
) -> None:
    if not linked_message_ids:
        return

    await db.execute(
        update(ConversationArtifact)
        .where(
            ConversationArtifact.conversation_id == conversation_id,
            private_scope_predicate(ConversationArtifact, context),
            ConversationArtifact.assistant_msg_id == assistant_msg_id,
            ConversationArtifact.status != "deleted",
        )
        .values(linked_message_ids=linked_message_ids)
    )


async def finalize_artifacts_for_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
    assistant_msg_id: str,
    run_status: str,
) -> int:
    """Close artifact state for a terminal run.

    Artifacts are discovered after individual tool results and are therefore
    optimistically marked ``ready`` while the assistant turn is still running.
    If the run later ends as canceled/stale/failed, those optimistic ready
    rows should not render as healthy completed outputs.
    """

    if run_status not in {"canceled", "stale", "failed"}:
        return 0

    result = cast(
        CursorResult[Any],
        await db.execute(
            update(ConversationArtifact)
            .where(
                ConversationArtifact.conversation_id == conversation_id,
                private_scope_predicate(ConversationArtifact, context),
                ConversationArtifact.assistant_msg_id == assistant_msg_id,
                ConversationArtifact.status.in_(("writing", "ready")),
            )
            .values(status="failed")
        ),
    )
    return int(result.rowcount or 0)
