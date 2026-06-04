from __future__ import annotations

import uuid
from contextlib import suppress
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_api.service import utc_now_naive
from app.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.agent_api import AgentApiRun, AgentApiThread, AgentDeployment
from app.models.conversation import Conversation
from app.schemas.agent_api import AgentRunRequest, AgentThreadCreateRequest, AgentThreadResponse
from app.services import chat_service
from app.services.agent_invocation_service import (
    AgentInvocationPrincipal,
    build_agent_config_for_loaded_agent,
)


def external_run_id(row_id: uuid.UUID) -> str:
    return f"run_{row_id.hex}"


def external_thread_id(row_id: uuid.UUID) -> str:
    return f"thr_{row_id.hex}"


def ensure_key_can_access_deployment(
    principal: ApiKeyPrincipal,
    deployment_id: uuid.UUID,
) -> None:
    if principal.key.allow_all_deployments:
        return
    allowed = {link.deployment_id for link in principal.key.deployment_links}
    if deployment_id not in allowed:
        raise ForbiddenError(
            "AGENT_API_DEPLOYMENT_FORBIDDEN",
            "API key cannot access this deployment",
        )


async def resolve_deployment_for_agent_id(
    db: AsyncSession,
    principal: ApiKeyPrincipal,
    agent_id: str,
    *,
    required_scope: str,
) -> AgentDeployment:
    principal.require_scope(required_scope)

    conditions = [AgentDeployment.public_id == agent_id]
    with suppress(ValueError):
        conditions.append(AgentDeployment.agent_id == uuid.UUID(agent_id))

    result = await db.execute(
        select(AgentDeployment)
        .where(AgentDeployment.user_id == principal.user_id)
        .where(AgentDeployment.status == "active")
        .where(or_(*conditions))
        .options(selectinload(AgentDeployment.agent))
    )
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")

    ensure_key_can_access_deployment(principal, deployment.id)

    if required_scope == "stream" and not deployment.allow_streaming:
        raise ForbiddenError("AGENT_API_STREAM_DISABLED", "streaming is disabled")
    if required_scope == "background" and not deployment.allow_background:
        raise ForbiddenError("AGENT_API_BACKGROUND_DISABLED", "background is disabled")
    return deployment


async def create_api_conversation(
    db: AsyncSession,
    deployment: AgentDeployment,
    *,
    title: str | None = None,
) -> Conversation:
    conversation = Conversation(
        agent_id=deployment.agent_id,
        title=title or "API conversation",
        source="api",
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
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
        title="API thread",
    )
    thread_id = uuid.uuid4()
    row = AgentApiThread(
        id=thread_id,
        public_id=external_thread_id(thread_id),
        user_id=principal.user_id,
        deployment_id=deployment.id,
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
        agent_id=thread.deployment.public_id,
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
    result = await db.execute(
        select(AgentApiThread)
        .where(AgentApiThread.public_id == thread_id, AgentApiThread.user_id == principal.user_id)
        .options(
            selectinload(AgentApiThread.deployment).selectinload(AgentDeployment.agent),
            selectinload(AgentApiThread.conversation),
        )
    )
    thread = result.scalar_one_or_none()
    if thread is None:
        raise NotFoundError("AGENT_API_THREAD_NOT_FOUND", "thread not found")
    ensure_key_can_access_deployment(principal, thread.deployment_id)
    return thread


async def create_run_row(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    conversation_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
    mode: str,
    input_payload: dict[str, Any],
    metadata: dict[str, Any] | None,
) -> AgentApiRun:
    row = AgentApiRun(
        public_id=f"run_{uuid.uuid4().hex}",
        user_id=principal.user_id,
        api_key_id=principal.key.id,
        deployment_id=deployment.id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        mode=mode,
        status="running",
        input=input_payload,
        meta=metadata,
        started_at=utc_now_naive(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def mark_run_succeeded(
    db: AsyncSession, run: AgentApiRun, *, output: dict[str, Any]
) -> AgentApiRun:
    run.status = "succeeded"
    run.output = output
    run.finished_at = utc_now_naive()
    await db.commit()
    await db.refresh(run)
    return run


async def mark_run_failed(
    db: AsyncSession,
    run: AgentApiRun,
    *,
    code: str,
    message: str,
) -> AgentApiRun:
    run.status = "failed"
    run.error_code = code
    run.error_message = message
    run.finished_at = utc_now_naive()
    await db.commit()
    await db.refresh(run)
    return run


async def build_config_for_run(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    conversation: Conversation,
    external_user: str | None,
):
    agent = await chat_service.get_agent_with_tools(db, deployment.agent_id, principal.user_id)
    if agent is None:
        raise NotFoundError("AGENT_NOT_FOUND", "agent not found")
    return await build_agent_config_for_loaded_agent(
        db,
        agent,
        thread_id=str(conversation.id),
        principal=AgentInvocationPrincipal.api_key(
            key_id=principal.key.id,
            owner_user_id=principal.user_id,
            external_user_id=external_user,
        ),
        source="api",
    )


def validate_messages(request: AgentRunRequest) -> list[dict[str, Any]]:
    messages = request.input.messages
    if not messages:
        raise ValidationError("AGENT_API_MESSAGES_REQUIRED", "messages are required")
    for message in messages:
        if not isinstance(message.get("role"), str) or "content" not in message:
            raise ValidationError("AGENT_API_MESSAGE_INVALID", "message role/content required")
    return messages
