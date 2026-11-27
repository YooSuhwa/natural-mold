"""Project one eligible, stored chat input for a fresh AG-UI retry."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import content_to_text
from app.authz.conversation_input_scope import conversation_input_parent
from app.authz.conversation_run_scope import owned_run_predicate, private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_parent_scoped
from app.chat_recovery.model_attempts import AttemptStop
from app.chat_recovery.receipts import blocking_receipt_code
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput, JsonValue
from app.schemas.chat_resource_context import PUBLIC_RESOURCE_CONTEXT_KEY
from app.services.ag_ui_start_input import start_message_id
from app.services.chat_resource_context_payload import public_resource_context_from_payload
from app.services.conversation_run_queue_mutations import (
    queue_input_not_found,
    require_queue_executable,
)
from app.services.conversation_run_queue_payload import validate_queue_input_payload
from app.services.partial_model_output import PARTIAL_OUTPUT_KEY, PARTIAL_STREAM_ERROR

# These failures require review or a continuation from the saved checkpoint.
_REVIEW_REQUIRED = {
    "recovery_effect_unconfirmed",
    "recovery_steer_unconfirmed",
    "recovery_output_started",
    AttemptStop.LIMIT,
    AttemptStop.UNPROVEN,
    PARTIAL_STREAM_ERROR,
}


def _retry_conflict() -> HTTPException:
    return HTTPException(409, "This input cannot be retried as a new chat turn")


def _retry_payload(
    queued: ConversationRunInput, public_run_id: str
) -> tuple[dict[str, JsonValue], list[UUID]]:
    stored = queued.input_payload
    if not isinstance(stored, dict):
        raise _retry_conflict()
    messages = stored.get("messages")
    last = messages[-1] if isinstance(messages, list) and messages else None
    if not isinstance(last, dict) or last.get("role", last.get("type")) not in ("user", "human"):
        raise _retry_conflict()
    if last.get("subagentRunId") is not None or last.get("subagent_run_id") is not None:
        raise _retry_conflict()
    content = last.get("content")
    if not isinstance(content, (str, list)):
        raise _retry_conflict()
    # Legacy browser messages include text blocks and URLs. Only server-linked
    # uploads, verified by the admission service, can supply media to a retry.
    text = content_to_text(content)
    raw_ids = queued.attachment_ids
    if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
        raise _retry_conflict()
    try:
        uploads = list(dict.fromkeys(UUID(value) for value in raw_ids))
    except ValueError as exc:
        raise _retry_conflict() from exc
    refs = public_resource_context_from_payload(stored)
    if not text.strip() and not uploads and not refs:
        raise _retry_conflict()

    stored_id = last.get("id")
    message_id = stored_id if isinstance(stored_id, str) and stored_id else str(queued.id)
    payload: dict[str, JsonValue] = {
        "messages": [
            {
                "id": start_message_id(queued.conversation_id, public_run_id, message_id),
                "role": "user",
                "content": text,
            }
        ]
    }
    if refs:
        payload[PUBLIC_RESOURCE_CONTEXT_KEY] = [
            cast("dict[str, JsonValue]", dict(ref)) for ref in refs
        ]
    return validate_queue_input_payload(payload), uploads


async def load_retry_start(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation: Conversation,
    input_id: UUID,
    public_run_id: str,
) -> tuple[dict[str, JsonValue], list[UUID]]:
    """Resolve the UI's most recent failed turn under the admission transaction."""
    agent_id = await require_queue_executable(db, conversation.id, context)
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        raise _retry_conflict()
    # The input must still belong to the latest run visible in this conversation.
    # Read identity and retry markers before loading any private input or tool result.
    latest_run = (
        select(ConversationRun.id)
        .where(ConversationRun.conversation_id == conversation.id, private_run_predicate(context))
        .order_by(ConversationRun.created_at.desc(), ConversationRun.id.desc())
        .limit(1)
        .correlate(None)
        .scalar_subquery()
    )
    eligibility = (
        await db.execute(
            private_parent_scoped(
                select(
                    ConversationRun.id,
                    ConversationRun.status,
                    ConversationRun.is_active,
                    ConversationRun.source,
                    ConversationRun.run_source,
                    ConversationRun.error_code,
                    ConversationRun.metadata_json[PARTIAL_OUTPUT_KEY].as_string().is_not(None),
                )
                .select_from(ConversationRunInput)
                .join(ConversationRun, ConversationRun.id == ConversationRunInput.run_id)
                .where(
                    ConversationRunInput.id == input_id,
                    ConversationRunInput.conversation_id == conversation.id,
                    ConversationRunInput.agent_id == agent_id,
                    ConversationRun.id == latest_run,
                    owned_run_predicate(context),
                    ConversationRun.conversation_id == ConversationRunInput.conversation_id,
                    ConversationRun.agent_id == ConversationRunInput.agent_id,
                    # Unattributed legacy runs inherit scope from the same private parent.
                    or_(
                        and_(
                            ConversationRun.org_id == context.org_id,
                            ConversationRun.tenant_id == context.tenant_id,
                        ),
                        and_(ConversationRun.org_id.is_(None), ConversationRun.tenant_id.is_(None)),
                    ),
                ),
                conversation_input_parent(),
                context,
            )
        )
    ).one_or_none()
    if eligibility is None:
        raise queue_input_not_found()
    run_id, status, active, source, attribution, error, partial = eligibility
    if (
        status != "failed"
        or active
        or source != "chat"
        or attribution not in (None, "CHAT")
        or error in _REVIEW_REQUIRED
        or partial
        or await blocking_receipt_code(db, run_id) == "recovery_effect_unconfirmed"
    ):
        raise _retry_conflict()
    queued = await db.scalar(
        private_parent_scoped(
            select(ConversationRunInput).where(
                ConversationRunInput.id == input_id,
                ConversationRunInput.run_id == run_id,
                ConversationRunInput.source == "chat",
                ConversationRunInput.checkpoint_id.is_(None),
                ConversationRunInput.status.in_(("claimed", "failed")),
            ),
            conversation_input_parent(),
            context,
        )
    )
    if queued is None:
        raise _retry_conflict()
    return _retry_payload(queued, public_run_id)
