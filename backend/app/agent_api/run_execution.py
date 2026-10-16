from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api.access import authorize_deployment
from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_api.service import utc_now_naive
from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.run_error_codes import inherited_envelope_code
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.agent_runtime.streaming import StreamErrorRecord
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.config import settings
from app.exceptions import ConflictError, NotFoundError
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_refs import JsonValue
from app.services import chat_service
from app.services.agent_execution_runtime import (
    RuntimeOwnershipRequest,
    run_owned_execution,
    stream_owned_execution,
)
from app.services.agent_invocation_service import AgentInvocationPrincipal
from app.services.conversation_runtime_policy import snapshot_from_conversation


def _runtime_attr(name: str) -> Any:
    """Return ``runtime_service.<name>`` as currently bound (tests patch it)."""

    from app.agent_api import runtime_service

    return getattr(runtime_service, name)


def _prepared() -> Any:
    """Call-time import: ``prepared_execution`` depends on this module's builder."""

    from app.agent_api import prepared_execution

    return prepared_execution


@dataclass(frozen=True, slots=True)
class ApiOwnedExecution:
    conversation_run_id: uuid.UUID
    conversation_id: uuid.UUID
    root_agent_id: uuid.UUID
    context: OrgContext
    public_run_id: str
    config: AgentConfig
    messages: list[RuntimeMessage]


async def mark_run_succeeded(
    db: AsyncSession, run: AgentApiRun, *, output: dict[str, JsonValue]
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
    if run.conversation_run_id is not None:
        # A linked envelope inherits the shared denial code of its run (I24 M2).
        run_error = await db.scalar(
            select(ConversationRun.error_code).where(ConversationRun.id == run.conversation_run_id)
        )
        code = inherited_envelope_code(run_error) or code
    run.error_code = code
    run.error_message = message
    run.finished_at = utc_now_naive()
    await db.commit()
    await db.refresh(run)
    return run


async def execute_owned_wait(request: ApiOwnedExecution) -> str:
    if settings.prepared_run_lease_enabled:
        prepared = await _prepared().materialize_api_run(request, "invoke")
        if prepared is not None:
            return await _prepared().execute_prepared_wait(request, prepared)
    ownership = RuntimeOwnershipRequest(
        request.conversation_run_id,
        request.conversation_id,
        request.root_agent_id,
        request.context,
    )
    operation = partial(
        _runtime_attr("execute_agent_invoke"),
        request.config,
        request.messages,
        run_id=request.public_run_id,
        moldy_source="api",
    )
    return await run_owned_execution(ownership, operation)


async def stream_owned_run(request: ApiOwnedExecution) -> AsyncGenerator[str, None]:
    if settings.prepared_run_lease_enabled:
        prepared = await _prepared().materialize_api_run(request, "stream")
        if prepared is not None:
            async with aclosing(_prepared().stream_prepared_run(request, prepared)) as chunks:
                async for chunk in chunks:
                    yield chunk
            return
    ownership = RuntimeOwnershipRequest(
        request.conversation_run_id,
        request.conversation_id,
        request.root_agent_id,
        request.context,
    )

    async def operation() -> AsyncGenerator[str, None]:
        errors: list[StreamErrorRecord] = []
        saw_error_event = False
        async for chunk in _runtime_attr("execute_agent_stream")(
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
    include_subagents: bool = True,
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
    return await _runtime_attr("build_agent_config_for_loaded_agent")(
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
        **({} if include_subagents else {"include_subagents": False}),
    )
