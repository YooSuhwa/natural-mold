"""Atomically accept a verified continuation of an interrupted conversation run."""

from __future__ import annotations

from collections.abc import Sequence

from ag_ui.core import ResumeEntry
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.services import chat_service
from app.services.ag_ui_commands import (
    PROJECTION_VERSION,
    AgUiRunBinding,
    CommandAdmission,
    bind_command,
)
from app.services.ag_ui_deferred_delivery import seal_ag_ui_delivery
from app.services.ag_ui_resume import claim_verified_resume, verify_ag_ui_resume
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_resume_types import (
    resume_decision_metadata,
    resume_run_interrupt_id,
)
from app.services.conversation_session_consent import (
    SessionConsentPayload,
    SessionConsentTarget,
    apply_session_consent_decisions,
)


async def accept_resume(
    db: AsyncSession,
    *,
    context: OrgContext,
    user: CurrentUser,
    conversation: Conversation,
    request: Request,
    entries: Sequence[ResumeEntry],
    admission: CommandAdmission,
) -> AgUiRunBinding:
    verified = await verify_ag_ui_resume(
        db, conversation=conversation, context=context, entries=entries
    )
    agent_id = await chat_service.prepare_resumed_conversation(
        db, conversation.id, context, verified.resume, commit_policy_snapshot=False
    )
    parent = verified.parent_run
    interrupt_id = resume_run_interrupt_id(verified.resume, parent.interrupt_id)
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            run_source="resume",
            input_preview=None,
            parent_run_id=parent.id,
            interrupt_id=interrupt_id,
            run_metadata={
                "ag_ui_projection_version": PROJECTION_VERSION,
                "protocol": "langgraph_v3",
                "parent_run_id": str(parent.id),
                **resume_decision_metadata(verified.resume, verified.pending_interrupts),
            },
        ),
    )
    await claim_verified_resume(
        db, verified=verified, actor_id=user.id, resumed_run_id=accepted.run_id
    )
    consented_tools = await apply_session_consent_decisions(
        db,
        SessionConsentTarget(conversation.id, context),
        SessionConsentPayload(verified.resume, verified.pending_interrupts),
    )
    await chat_service.touch_conversation(db, conversation.id, context)
    await record_conversation_audit(
        db,
        request=request,
        user=user,
        action="conversation.message_resume",
        conversation_id=conversation.id,
        agent_id=agent_id,
        metadata={
            "source": "ag_ui",
            "interrupt_id": interrupt_id,
            **({"consented_tools": consented_tools} if consented_tools else {}),
        },
    )
    private_delivery = await seal_ag_ui_delivery(
        db,
        context=context,
        conversation_id=conversation.id,
        public_run_id=admission.public_run_id,
        run_id=accepted.run_id,
        source="resume",
        parent_run_id=parent.id,
        checkpoint_id=verified.checkpoint_id,
        payload={
            "response": verified.input_payload,
            "interruptIds": [entry["id"] for entry in verified.pending_interrupts],
            "approvalRequestIds": {
                key: str(value) for key, value in verified.approval_request_ids.items()
            },
            "inheritedApprovalRequestIds": {
                key: [str(identifier) for identifier in identifiers]
                for key, identifiers in verified.inherited_approval_request_ids.items()
            },
        },
    )
    return await bind_command(
        db,
        admission,
        run_id=accepted.run_id,
        checkpoint_id=verified.checkpoint_id,
        delivery_encrypted=private_delivery,
    )
