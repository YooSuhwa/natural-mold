from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.access import authorize_deployment, ensure_key_can_access_deployment
from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.authz.agent_api_scope import private_api_thread_parents_predicate
from app.exceptions import ConflictError, NotFoundError
from app.models.agent_api import AgentApiThread, AgentDeployment
from app.models.conversation import Conversation
from app.schemas.agent_api import AgentThreadCreateRequest, AgentThreadResponse
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy


def external_thread_id(row_id: uuid.UUID) -> str:
    return f"thr_{row_id.hex}"


async def create_api_conversation(
    db: AsyncSession,
    deployment: AgentDeployment,
    *,
    principal: ApiKeyPrincipal,
    title: str | None = None,
) -> Conversation:
    authorized = await authorize_deployment(db, principal, deployment.id)
    conversation = await chat_service.create_conversation(
        db,
        authorized.deployment.agent_id,
        title or "API conversation",
        context=authorized.principal.context,
        source="api",
    )
    try:
        await ensure_conversation_runtime_policy(
            db,
            ConversationPolicyTarget(conversation.id, authorized.principal.context),
        )
    except RuntimePolicySnapshotError as exc:
        raise ConflictError(exc.code, exc.code) from None
    return conversation


async def create_thread(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    request: AgentThreadCreateRequest,
) -> AgentApiThread:
    conversation = await create_api_conversation(
        db,
        deployment,
        principal=principal,
        title="API thread",
    )
    thread_id = uuid.uuid4()
    row = AgentApiThread(
        id=thread_id,
        public_id=external_thread_id(thread_id),
        user_id=principal.user_id,
        deployment_id=deployment.id,
        deployment_public_id_snapshot=deployment.public_id,
        conversation_id=conversation.id,
        external_user=request.user,
        meta=request.metadata,
    )
    db.add(row)
    await db.commit()
    result = await db.execute(
        select(AgentApiThread)
        .where(AgentApiThread.id == row.id)
        .options(
            selectinload(AgentApiThread.deployment).selectinload(AgentDeployment.agent),
            selectinload(AgentApiThread.conversation),
        )
    )
    return result.scalar_one()


def thread_to_response(thread: AgentApiThread) -> AgentThreadResponse:
    return AgentThreadResponse(
        id=thread.public_id,
        agent_id=thread.deployment_public_id_snapshot,
        conversation_id=thread.conversation_id,
        user=thread.external_user,
        metadata=thread.meta,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


async def get_thread_for_public_id(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    thread_id: str,
) -> AgentApiThread:
    principal = await refresh_api_key_principal(db, principal)
    result = await db.execute(
        select(AgentApiThread)
        .where(
            AgentApiThread.public_id == thread_id,
            AgentApiThread.user_id == principal.user_id,
            private_api_thread_parents_predicate(principal.context),
        )
        .options(
            selectinload(AgentApiThread.deployment).selectinload(AgentDeployment.agent),
            selectinload(AgentApiThread.conversation),
        )
        .execution_options(populate_existing=True, autoflush=False)
    )
    thread = result.scalar_one_or_none()
    if thread is None:
        raise NotFoundError("AGENT_API_THREAD_NOT_FOUND", "thread not found")
    try:
        ensure_key_can_access_deployment(principal, thread.deployment)
    except NotFoundError:
        raise NotFoundError("AGENT_API_THREAD_NOT_FOUND", "thread not found") from None
    return thread
