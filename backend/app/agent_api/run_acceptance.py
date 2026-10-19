from __future__ import annotations

import uuid
from typing import Literal, TypedDict

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api.access import authorize_deployment
from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_api.thread_runtime import create_api_conversation
from app.agent_runtime.message_utils import RuntimeMessage, is_runtime_message
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.authz.scoping import private_scoped
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.agent_api import AgentApiRun, AgentApiThread, AgentDeployment
from app.models.conversation import Conversation
from app.schemas.agent_api import AgentRunRequest
from app.schemas.conversation_refs import JsonValue
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy


class AgentApiRunInputPayload(TypedDict):
    messages: list[RuntimeMessage]


_API_MESSAGES = TypeAdapter(list[dict[str, JsonValue]])


def external_run_id(row_id: uuid.UUID) -> str:
    return f"run_{row_id.hex}"


async def create_run_row(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    conversation_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
    mode: str,
    input_payload: AgentApiRunInputPayload,
    metadata: dict[str, JsonValue] | None,
) -> AgentApiRun:
    authorized = await authorize_deployment(db, principal, deployment.id)
    principal, deployment = authorized.principal, authorized.deployment
    scope_for_mode: dict[str, Literal["invoke", "stream"]] = {
        "wait": "invoke",
        "stream": "stream",
        "openai_stream": "stream",
    }
    operation = scope_for_mode.get(mode)
    if operation is None:
        raise ValidationError("AGENT_API_RUN_MODE_INVALID", "run mode is invalid")
    principal.require_scope(operation)
    if conversation_id is None and thread_id is not None:
        conversation_id = await db.scalar(
            select(AgentApiThread.conversation_id).where(
                AgentApiThread.id == thread_id,
                AgentApiThread.user_id == principal.user_id,
                AgentApiThread.deployment_id == deployment.id,
            )
        )
    if conversation_id is None:
        conversation_id = (
            await create_api_conversation(
                db,
                deployment,
                principal=principal,
                title="API run",
            )
        ).id
    if conversation_id is not None:
        conversation = await db.scalar(
            private_scoped(
                select(Conversation).where(Conversation.id == conversation_id),
                Conversation,
                principal.context,
            )
        )
        if conversation is None or conversation.agent_id != deployment.agent_id:
            raise NotFoundError("AGENT_API_THREAD_NOT_FOUND", "thread not found")
        try:
            await ensure_conversation_runtime_policy(
                db, ConversationPolicyTarget(conversation_id, principal.context)
            )
        except RuntimePolicySnapshotError as exc:
            raise ConflictError(exc.code, exc.code) from None
    row = AgentApiRun(
        public_id=f"run_{uuid.uuid4().hex}",
        user_id=principal.user_id,
        api_key_id=principal.key.id,
        deployment_id=deployment.id,
        deployment_public_id_snapshot=deployment.public_id,
        tenant_id=deployment.tenant_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        mode=mode,
        status="pending",
        input=input_payload,
        meta=metadata,
    )
    db.add(row)
    await db.flush()
    await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=deployment.agent_id,
            context=principal.context,
            source="api",
            input_preview=str(input_payload)[:500],
            envelope=ExecutionEnvelopeRef(kind="api", envelope_id=row.id),
        ),
    )
    await db.commit()
    db.expunge(row)
    accepted = await db.scalar(select(AgentApiRun).where(AgentApiRun.id == row.id))
    if accepted is None:
        raise RuntimeError("accepted API run disappeared after commit")
    return accepted


def validate_messages(request: AgentRunRequest) -> list[RuntimeMessage]:
    try:
        messages = _API_MESSAGES.validate_python(request.input.messages, strict=True)
    except PydanticValidationError:
        raise ValidationError(
            "AGENT_API_MESSAGE_INVALID",
            "messages must contain JSON-compatible values",
        ) from None
    if not messages:
        raise ValidationError("AGENT_API_MESSAGES_REQUIRED", "messages are required")
    runtime_messages: list[RuntimeMessage] = []
    for message in messages:
        if not is_runtime_message(message):
            raise ValidationError("AGENT_API_MESSAGE_INVALID", "message role/content required")
        runtime_messages.append(message)
    return runtime_messages
