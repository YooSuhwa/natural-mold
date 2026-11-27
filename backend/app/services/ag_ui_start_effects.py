"""Conversation effects shared by both chat protocols within the accepting transaction."""

from uuid import UUID

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.authz.attachment_scope import private_attachment_predicate
from app.authz.conversation_scope import require_conversation_agent_id, require_private_conversation
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import file_not_found
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_attachment import MessageAttachment
from app.schemas.conversation_scope import ConversationAutoTitle, ConversationPromotion
from app.services import chat_service
from app.services.chat.image_description_admission import capture_admission, freeze_direct_admission
from app.services.conversation_audit_service import record_conversation_audit


async def authorize_start_attachments(
    db: AsyncSession,
    context: OrgContext,
    conversation_id: UUID,
    attachment_ids: list[UUID],
    *,
    require_linked: bool = False,
) -> None:
    """Reserve every upload for this transaction before authorizing any admission effect."""
    if not attachment_ids:
        return
    await require_private_conversation(db, conversation_id, context)
    expected = set(attachment_ids)
    binding = MessageAttachment.conversation_id == conversation_id
    if not require_linked:
        binding = or_(binding, MessageAttachment.conversation_id.is_(None))
    authorized = await db.scalars(
        select(MessageAttachment.id)
        .where(
            MessageAttachment.id.in_(expected),
            private_attachment_predicate(context),
            binding,
        )
        .order_by(MessageAttachment.id)
        .with_for_update(of=MessageAttachment)
        .execution_options(autoflush=False)
    )
    if set(authorized) != expected:
        raise file_not_found()


async def apply_start_effects(
    db: AsyncSession,
    *,
    context: OrgContext,
    user: CurrentUser,
    conversation: Conversation,
    request: Request | None,
    message_preview: str | None,
    attachment_ids: list[UUID],
    source: str,
    cfg: AgentConfig,
    queued_input: ConversationRunInput | None = None,
    run_id: UUID | None = None,
) -> None:
    if conversation.source == "draft":
        await chat_service.promote_draft_conversation(
            db, ConversationPromotion(conversation.id, context, message_preview)
        )
    elif message_preview:
        await chat_service.maybe_set_auto_title(
            db, ConversationAutoTitle(conversation.id, context, message_preview)
        )
    await chat_service.touch_conversation(db, conversation.id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_send",
        conversation_id=conversation.id,
        agent_id=require_conversation_agent_id(conversation),
        metadata={
            "content_length": len(message_preview or ""),
            "attachment_count": len(attachment_ids),
            "source": source,
        },
    )
    if not attachment_ids:
        return
    await chat_service.link_attachments_to_conversation(
        db, conversation_id=conversation.id, context=context, attachment_ids=attachment_ids
    )
    if queued_input is not None:
        queued_input.image_description_admission = await capture_admission(
            db,
            context=context,
            conversation_id=conversation.id,
            root_model_id=cfg.model_id,
            attachment_ids=attachment_ids,
        )
    elif run_id is not None:
        await freeze_direct_admission(
            db,
            context=context,
            conversation_id=conversation.id,
            run_id=run_id,
            root_model_id=cfg.model_id,
            attachment_ids=attachment_ids,
        )
    if cfg.runtime_profile == "skill_builder" and cfg.draft_workspace_path:
        from app.services import skill_draft_workspace

        await skill_draft_workspace.copy_conversation_attachments_to_inputs(
            db,
            storage_path=cfg.draft_workspace_path,
            conversation_id=conversation.id,
            attachment_ids=attachment_ids,
            context=context,
            stable_names=source != "langgraph_protocol",
        )
