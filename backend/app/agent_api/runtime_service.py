from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.access import authorize_deployment, ensure_key_can_access_deployment
from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.agent_api.service import utc_now_naive
from app.agent_runtime.agent_stream_runner import execute_agent_invoke, execute_agent_stream
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.agent_runtime.streaming import StreamErrorRecord
from app.authz.agent_api_scope import (
    private_api_thread_parents_predicate,
    private_deployment_predicate,
)
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models.agent_api import AgentApiRun, AgentApiThread, AgentDeployment
from app.models.conversation import Conversation
from app.schemas.agent_api import AgentRunRequest, AgentThreadCreateRequest, AgentThreadResponse
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from app.services.agent_execution_runtime import (
    RuntimeOwnershipRequest,
    run_owned_execution,
    stream_owned_execution,
)
from app.services.agent_invocation_service import (
    AgentInvocationPrincipal,
    build_agent_config_for_loaded_agent,
)
from app.services.conversation_runtime_policy import (
    ensure_conversation_runtime_policy,
    snapshot_from_conversation,
)


@dataclass(frozen=True, slots=True)
class ApiOwnedExecution:
    conversation_run_id: uuid.UUID
    conversation_id: uuid.UUID
    root_agent_id: uuid.UUID
    context: OrgContext
    public_run_id: str
    config: AgentConfig
    messages: list[dict[str, Any]]


def external_run_id(row_id: uuid.UUID) -> str:
    return f"run_{row_id.hex}"


def external_thread_id(row_id: uuid.UUID) -> str:
    return f"thr_{row_id.hex}"


async def resolve_deployment_for_agent_id(
    db: AsyncSession,
    principal: ApiKeyPrincipal,
    agent_id: str,
    *,
    required_scope: str,
) -> AgentDeployment:
    principal = await refresh_api_key_principal(db, principal)
    principal.require_scope(required_scope)

    conditions = [AgentDeployment.public_id == agent_id]
    with suppress(ValueError):
        conditions.append(AgentDeployment.agent_id == uuid.UUID(agent_id))

    result = await db.execute(
        select(AgentDeployment)
        .where(private_deployment_predicate(principal.context))
        .where(AgentDeployment.status == "active")
        .where(or_(*conditions))
        .options(selectinload(AgentDeployment.agent))
        .execution_options(populate_existing=True, autoflush=False)
    )
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")

    ensure_key_can_access_deployment(principal, deployment)

    if required_scope == "stream" and not deployment.allow_streaming:
        raise ForbiddenError("AGENT_API_STREAM_DISABLED", "streaming is disabled")
    if required_scope == "background" and not deployment.allow_background:
        raise ForbiddenError("AGENT_API_BACKGROUND_DISABLED", "background is disabled")
    return deployment


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


async def mark_run_succeeded(
    db: AsyncSession, run: AgentApiRun, *, output: dict[str, Any]
) -> AgentApiRun:
    await db.refresh(run)
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
    await db.refresh(run)
    run.error_code = code
    run.error_message = message
    run.finished_at = utc_now_naive()
    await db.commit()
    await db.refresh(run)
    return run


async def execute_owned_wait(request: ApiOwnedExecution) -> str:
    ownership = RuntimeOwnershipRequest(
        request.conversation_run_id,
        request.conversation_id,
        request.root_agent_id,
        request.context,
    )
    operation = partial(
        execute_agent_invoke,
        request.config,
        request.messages,
        run_id=request.public_run_id,
        moldy_source="api",
    )
    return await run_owned_execution(ownership, operation)


async def stream_owned_run(request: ApiOwnedExecution) -> AsyncGenerator[str, None]:
    ownership = RuntimeOwnershipRequest(
        request.conversation_run_id,
        request.conversation_id,
        request.root_agent_id,
        request.context,
    )

    async def operation() -> AsyncGenerator[str, None]:
        errors: list[StreamErrorRecord] = []
        saw_error_event = False
        async for chunk in execute_agent_stream(
            request.config,
            request.messages,
            run_id=request.public_run_id,
            moldy_source="api",
            error_sink=errors,
        ):
            if chunk.lstrip().startswith("event: error"):
                saw_error_event = True
                continue
            yield chunk
        if errors:
            raise RuntimeError(errors[0].message)
        if saw_error_event:
            raise RuntimeError("Agent run failed")

    owned_stream = stream_owned_execution(ownership, operation)
    try:
        async for chunk in owned_stream:
            yield chunk
    finally:
        await owned_stream.aclose()


async def build_config_for_run(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    conversation: Conversation,
    external_user: str | None,
    required_scope: Literal["invoke", "stream"],
):
    authorized = await authorize_deployment(db, principal, deployment.id)
    principal, deployment = authorized.principal, authorized.deployment
    principal.require_scope(required_scope)
    scoped_conversation = await db.scalar(
        private_scoped(
            select(Conversation).where(Conversation.id == conversation.id),
            Conversation,
            principal.context,
        )
    )
    if scoped_conversation is None or scoped_conversation.agent_id != deployment.agent_id:
        raise NotFoundError("AGENT_API_THREAD_NOT_FOUND", "thread not found")
    conversation = scoped_conversation
    await chat_service.require_executable_conversation(
        db,
        conversation.id,
        principal.context,
    )
    try:
        runtime_policy = snapshot_from_conversation(conversation)
    except RuntimePolicySnapshotError as exc:
        raise ConflictError(exc.code, exc.code) from None
    if runtime_policy is None:
        raise ConflictError(
            "RUNTIME_POLICY_SNAPSHOT_INVALID",
            "RUNTIME_POLICY_SNAPSHOT_INVALID",
        )
    agent = await chat_service.get_agent_with_tools(db, deployment.agent_id, principal.context)
    if agent is None:
        raise NotFoundError("AGENT_NOT_FOUND", "agent not found")
    return await build_agent_config_for_loaded_agent(
        db,
        agent,
        context=principal.context,
        thread_id=str(conversation.id),
        principal=AgentInvocationPrincipal.api_key(
            key_id=principal.key.id,
            owner_user_id=principal.user_id,
            external_user_id=external_user,
        ),
        source="api",
        runtime_policy=runtime_policy,
    )


def validate_messages(request: AgentRunRequest) -> list[dict[str, Any]]:
    messages = request.input.messages
    if not messages:
        raise ValidationError("AGENT_API_MESSAGES_REQUIRED", "messages are required")
    for message in messages:
        if not isinstance(message.get("role"), str) or "content" not in message:
            raise ValidationError("AGENT_API_MESSAGE_INVALID", "message role/content required")
    return messages
