from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api import runtime_service
from app.agent_api.dependencies import ApiKeyPrincipal, get_api_key_principal
from app.agent_api.sse_adapter import (
    adapt_internal_stream,
    adapt_internal_stream_to_openai,
    format_openai_data_sse,
)
from app.agent_runtime.executor import execute_agent_invoke, execute_agent_stream
from app.dependencies import get_db
from app.exceptions import AppError
from app.models.agent_api import AgentDeployment
from app.schemas.agent_api import (
    AgentRunInput,
    AgentRunRequest,
    AgentRunResponse,
    AgentThreadCreateRequest,
    AgentThreadResponse,
)
from app.services import audit_service

router = APIRouter(prefix="/v1", tags=["agent-runtime-api"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _request_metadata_keys(request: AgentRunRequest) -> list[str]:
    return sorted(str(key) for key in (request.metadata or {}))


async def _record_agent_api_run_audit(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    request: AgentRunRequest,
    run,
    action: str,
    outcome: str,
    reason_code: str | None = None,
    reason_message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="api_key",
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.key.id,
        actor_label=principal.key.name,
        owner_user_id=principal.user_id,
        action=action,
        target_type="agent_api_run",
        target_id=run.public_id,
        target_name_snapshot=deployment.public_id,
        target_owner_user_id=principal.user_id,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
        run_id=run.public_id,
        metadata={
            "deployment_id": str(deployment.id),
            "agent_id": str(deployment.agent_id),
            "public_id": deployment.public_id,
            "mode": run.mode,
            "conversation_id": str(run.conversation_id),
            "thread_id": str(run.thread_id) if run.thread_id else None,
            "message_count": len(request.input.messages),
            "external_user_present": bool(request.user),
            "request_metadata_keys": _request_metadata_keys(request),
            **(metadata or {}),
        },
    )


async def _record_agent_api_thread_audit(
    db: AsyncSession,
    *,
    principal: ApiKeyPrincipal,
    deployment: AgentDeployment,
    thread,
    request: AgentThreadCreateRequest,
    action: str,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="api_key",
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.key.id,
        actor_label=principal.key.name,
        owner_user_id=principal.user_id,
        action=action,
        target_type="agent_api_thread",
        target_id=thread.public_id,
        target_name_snapshot=deployment.public_id,
        target_owner_user_id=principal.user_id,
        outcome="success",
        metadata={
            "deployment_id": str(deployment.id),
            "agent_id": str(deployment.agent_id),
            "public_id": deployment.public_id,
            "conversation_id": str(thread.conversation_id),
            "external_user_present": bool(request.user),
            "request_metadata_keys": sorted(str(key) for key in (request.metadata or {})),
        },
    )


async def _mark_wait_run_failed_and_raise(
    db: AsyncSession,
    run,
    exc: Exception,
) -> None:
    if isinstance(exc, AppError):
        await runtime_service.mark_run_failed(
            db,
            run,
            code=exc.code,
            message=exc.message,
        )
        raise exc

    await runtime_service.mark_run_failed(
        db,
        run,
        code="AGENT_API_RUN_FAILED",
        message=str(exc),
    )
    raise AppError(
        code="AGENT_API_RUN_FAILED",
        message="Agent run failed",
        status=500,
    ) from exc


async def _mark_stream_run_failed_and_raise(
    db: AsyncSession,
    run,
    exc: Exception,
) -> None:
    if isinstance(exc, AppError):
        await runtime_service.mark_run_failed(
            db,
            run,
            code=exc.code,
            message=exc.message,
        )
        raise exc

    await runtime_service.mark_run_failed(
        db,
        run,
        code="AGENT_API_STREAM_FAILED",
        message=str(exc),
    )
    raise AppError(
        code="AGENT_API_STREAM_FAILED",
        message="Agent stream failed",
        status=500,
    ) from exc


@router.get("/health")
async def public_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/agents")
async def list_public_agents(
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> dict[str, list[dict[str, object]]]:
    principal.require_scope("read")
    if principal.key.allow_all_deployments:
        result = await db.execute(
            select(AgentDeployment)
            .where(
                AgentDeployment.user_id == principal.user_id,
                AgentDeployment.status == "active",
            )
            .options(selectinload(AgentDeployment.agent))
            .order_by(AgentDeployment.created_at.desc())
        )
        deployments = list(result.scalars().all())
    else:
        deployments = [
            link.deployment
            for link in principal.key.deployment_links
            if link.deployment is not None and link.deployment.status == "active"
        ]

    data: list[dict[str, object]] = []
    for deployment in deployments:
        agent = deployment.agent
        data.append(
            {
                "id": deployment.public_id,
                "agent_id": str(deployment.agent_id),
                "name": agent.name if agent is not None else deployment.public_id,
                "description": agent.description if agent is not None else None,
                "status": deployment.status,
                "capabilities": [
                    scope
                    for scope in ("invoke", "stream", "background")
                    if scope in set(principal.key.scopes or [])
                    and (
                        scope != "stream"
                        or deployment.allow_streaming
                    )
                    and (
                        scope != "background"
                        or deployment.allow_background
                    )
                ],
            }
        )
    return {"data": data}


@router.post("/threads", response_model=AgentThreadResponse, status_code=201)
async def create_thread(
    request: AgentThreadCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> AgentThreadResponse:
    deployment = await runtime_service.resolve_deployment_for_agent_id(
        db,
        principal,
        request.agent_id,
        required_scope="invoke",
    )
    thread = await runtime_service.create_thread(
        db,
        principal=principal,
        deployment=deployment,
        request=request,
    )
    await _record_agent_api_thread_audit(
        db,
        principal=principal,
        deployment=deployment,
        thread=thread,
        request=request,
        action="agent_api.thread_create",
    )
    await db.commit()
    return runtime_service.thread_to_response(thread)


@router.get("/threads/{thread_id}", response_model=AgentThreadResponse)
async def get_thread(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> AgentThreadResponse:
    principal.require_scope("read")
    thread = await runtime_service.get_thread_for_public_id(
        db,
        principal=principal,
        thread_id=thread_id,
    )
    return runtime_service.thread_to_response(thread)


@router.post("/runs/wait", response_model=AgentRunResponse)
async def run_wait(
    request: AgentRunRequest,
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> AgentRunResponse:
    deployment = await runtime_service.resolve_deployment_for_agent_id(
        db,
        principal,
        request.agent_id,
        required_scope="invoke",
    )
    messages = runtime_service.validate_messages(request)
    conversation = await runtime_service.create_api_conversation(
        db,
        deployment,
        title="API run",
    )
    run = await runtime_service.create_run_row(
        db,
        principal=principal,
        deployment=deployment,
        conversation_id=conversation.id,
        thread_id=None,
        mode="wait",
        input_payload={"messages": messages},
        metadata=request.metadata,
    )
    try:
        cfg = await runtime_service.build_config_for_run(
            db,
            principal=principal,
            deployment=deployment,
            conversation=conversation,
            external_user=request.user,
        )
        answer = await execute_agent_invoke(
            cfg,
            messages,
            run_id=run.public_id,
            moldy_source="api",
        )
        run = await runtime_service.mark_run_succeeded(
            db,
            run,
            output={"answer": answer},
        )
        await _record_agent_api_run_audit(
            db,
            principal=principal,
            deployment=deployment,
            request=request,
            run=run,
            action="agent_api.run_wait",
            outcome="success",
        )
        await db.commit()
    except Exception as exc:
        await _record_agent_api_run_audit(
            db,
            principal=principal,
            deployment=deployment,
            request=request,
            run=run,
            action="agent_api.run_wait",
            outcome="failure",
            reason_code=(
                exc.code if isinstance(exc, AppError) else "AGENT_API_RUN_FAILED"
            ),
            reason_message=getattr(exc, "message", str(exc)),
        )
        await db.commit()
        await _mark_wait_run_failed_and_raise(db, run, exc)
    return AgentRunResponse(
        id=run.public_id,
        thread_id=None,
        agent_id=deployment.public_id,
        status=run.status,
        output=run.output,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


async def _run_stream_response(
    *,
    request: AgentRunRequest,
    db: AsyncSession,
    principal: ApiKeyPrincipal,
    thread_id: str | None,
) -> StreamingResponse:
    deployment = await runtime_service.resolve_deployment_for_agent_id(
        db,
        principal,
        request.agent_id,
        required_scope="stream",
    )
    messages = runtime_service.validate_messages(request)
    if thread_id is not None:
        thread = await runtime_service.get_thread_for_public_id(
            db,
            principal=principal,
            thread_id=thread_id,
        )
        if thread.deployment_id != deployment.id:
            raise AppError(
                code="AGENT_API_THREAD_AGENT_MISMATCH",
                message="thread does not belong to this agent",
                status=409,
            )
        conversation = thread.conversation
        api_thread_id = thread.id
        public_thread_id = thread.public_id
        external_user = thread.external_user or request.user
    else:
        thread = None
        conversation = await runtime_service.create_api_conversation(
            db,
            deployment,
            title="API stream",
        )
        api_thread_id = None
        public_thread_id = None
        external_user = request.user

    run = await runtime_service.create_run_row(
        db,
        principal=principal,
        deployment=deployment,
        conversation_id=conversation.id,
        thread_id=api_thread_id,
        mode="stream",
        input_payload={"messages": messages},
        metadata=request.metadata,
    )
    try:
        cfg = await runtime_service.build_config_for_run(
            db,
            principal=principal,
            deployment=deployment,
            conversation=conversation,
            external_user=external_user,
        )
    except Exception as exc:
        await _record_agent_api_run_audit(
            db,
            principal=principal,
            deployment=deployment,
            request=request,
            run=run,
            action=(
                "agent_api.thread_run_stream"
                if thread_id is not None
                else "agent_api.run_stream"
            ),
            outcome="failure",
            reason_code=(
                exc.code if isinstance(exc, AppError) else "AGENT_API_STREAM_FAILED"
            ),
            reason_message=getattr(exc, "message", str(exc)),
        )
        await db.commit()
        await _mark_stream_run_failed_and_raise(db, run, exc)

    await _record_agent_api_run_audit(
        db,
        principal=principal,
        deployment=deployment,
        request=request,
        run=run,
        action=(
            "agent_api.thread_run_stream"
            if thread_id is not None
            else "agent_api.run_stream"
        ),
        outcome="success",
        metadata={"accepted": True},
    )
    await db.commit()

    async def generator() -> AsyncGenerator[str, None]:
        try:
            internal = execute_agent_stream(
                cfg,
                messages,
                run_id=run.public_id,
                moldy_source="api",
            )
            async for chunk in adapt_internal_stream(
                internal,
                run_id=run.public_id,
                thread_id=public_thread_id,
                agent_id=deployment.public_id,
            ):
                yield chunk
            await runtime_service.mark_run_succeeded(db, run, output={})
        except Exception as exc:  # noqa: BLE001 - stream errors must be visible to clients
            await runtime_service.mark_run_failed(
                db,
                run,
                code="AGENT_API_STREAM_FAILED",
                message=str(exc),
            )
            from app.agent_runtime.streaming import format_sse

            yield format_sse("error", {"message": str(exc)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _run_openai_stream_response(
    *,
    request: AgentRunRequest,
    db: AsyncSession,
    principal: ApiKeyPrincipal,
    model: str,
) -> StreamingResponse:
    deployment = await runtime_service.resolve_deployment_for_agent_id(
        db,
        principal,
        request.agent_id,
        required_scope="stream",
    )
    messages = runtime_service.validate_messages(request)
    conversation = await runtime_service.create_api_conversation(
        db,
        deployment,
        title="OpenAI-compatible stream",
    )
    run = await runtime_service.create_run_row(
        db,
        principal=principal,
        deployment=deployment,
        conversation_id=conversation.id,
        thread_id=None,
        mode="openai_stream",
        input_payload={"messages": messages},
        metadata=request.metadata,
    )
    try:
        cfg = await runtime_service.build_config_for_run(
            db,
            principal=principal,
            deployment=deployment,
            conversation=conversation,
            external_user=request.user,
        )
    except Exception as exc:
        await _mark_stream_run_failed_and_raise(db, run, exc)

    async def generator() -> AsyncGenerator[str, None]:
        try:
            internal = execute_agent_stream(
                cfg,
                messages,
                run_id=run.public_id,
                moldy_source="api",
            )
            async for chunk in adapt_internal_stream_to_openai(
                internal,
                run_id=run.public_id,
                model=model,
                created_at=run.created_at,
            ):
                yield chunk
            await runtime_service.mark_run_succeeded(db, run, output={})
        except Exception as exc:  # noqa: BLE001 - stream errors must be visible to clients
            await runtime_service.mark_run_failed(
                db,
                run,
                code="AGENT_API_STREAM_FAILED",
                message=str(exc),
            )
            yield format_openai_data_sse({"error": {"message": str(exc)}})
            yield format_openai_data_sse("[DONE]")

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/runs/stream")
async def run_stream(
    request: AgentRunRequest,
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> StreamingResponse:
    return await _run_stream_response(
        request=request,
        db=db,
        principal=principal,
        thread_id=None,
    )


@router.post("/threads/{thread_id}/runs/wait", response_model=AgentRunResponse)
async def thread_run_wait(
    thread_id: str,
    request: AgentRunRequest,
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> AgentRunResponse:
    deployment = await runtime_service.resolve_deployment_for_agent_id(
        db,
        principal,
        request.agent_id,
        required_scope="invoke",
    )
    thread = await runtime_service.get_thread_for_public_id(
        db,
        principal=principal,
        thread_id=thread_id,
    )
    if thread.deployment_id != deployment.id:
        raise AppError(
            code="AGENT_API_THREAD_AGENT_MISMATCH",
            message="thread does not belong to this agent",
            status=409,
        )
    messages = runtime_service.validate_messages(request)
    run = await runtime_service.create_run_row(
        db,
        principal=principal,
        deployment=deployment,
        conversation_id=thread.conversation_id,
        thread_id=thread.id,
        mode="wait",
        input_payload={"messages": messages},
        metadata=request.metadata,
    )
    try:
        cfg = await runtime_service.build_config_for_run(
            db,
            principal=principal,
            deployment=deployment,
            conversation=thread.conversation,
            external_user=thread.external_user or request.user,
        )
        answer = await execute_agent_invoke(
            cfg,
            messages,
            run_id=run.public_id,
            moldy_source="api",
        )
        run = await runtime_service.mark_run_succeeded(db, run, output={"answer": answer})
        await _record_agent_api_run_audit(
            db,
            principal=principal,
            deployment=deployment,
            request=request,
            run=run,
            action="agent_api.thread_run_wait",
            outcome="success",
        )
        await db.commit()
    except Exception as exc:
        await _record_agent_api_run_audit(
            db,
            principal=principal,
            deployment=deployment,
            request=request,
            run=run,
            action="agent_api.thread_run_wait",
            outcome="failure",
            reason_code=(
                exc.code if isinstance(exc, AppError) else "AGENT_API_RUN_FAILED"
            ),
            reason_message=getattr(exc, "message", str(exc)),
        )
        await db.commit()
        await _mark_wait_run_failed_and_raise(db, run, exc)
    return AgentRunResponse(
        id=run.public_id,
        thread_id=thread.public_id,
        agent_id=deployment.public_id,
        status=run.status,
        output=run.output,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.post("/threads/{thread_id}/runs/stream")
async def thread_run_stream(
    thread_id: str,
    request: AgentRunRequest,
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> StreamingResponse:
    return await _run_stream_response(
        request=request,
        db=db,
        principal=principal,
        thread_id=thread_id,
    )


@router.post("/agents/{public_id}/chat-messages", response_model=None)
async def dify_chat_messages(
    public_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> dict[str, Any] | StreamingResponse:
    query = str(payload.get("query") or "")
    response_mode = str(payload.get("response_mode") or "blocking")
    user = payload.get("user")
    conversation_id = payload.get("conversation_id")
    request = AgentRunRequest(
        agent_id=public_id,
        input=AgentRunInput(messages=[{"role": "user", "content": query}]),
        user=str(user) if user is not None else None,
        metadata=payload.get("inputs") if isinstance(payload.get("inputs"), dict) else None,
    )
    if response_mode == "streaming":
        if conversation_id:
            return await thread_run_stream(
                str(conversation_id),
                request,
                db,
                principal,
            )
        return await run_stream(request, db, principal)

    if conversation_id:
        run = await thread_run_wait(str(conversation_id), request, db, principal)
        thread_id = str(conversation_id)
    else:
        thread = await create_thread(
            AgentThreadCreateRequest(
                agent_id=public_id,
                user=str(user) if user is not None else None,
            ),
            db,
            principal,
        )
        run = await thread_run_wait(thread.id, request, db, principal)
        thread_id = thread.id
    return {
        "answer": (run.output or {}).get("answer", ""),
        "conversation_id": thread_id,
        "message_id": run.id,
        "usage": {},
    }


@router.post("/workflows/run", response_model=None)
async def dify_workflow_run(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> dict[str, Any] | StreamingResponse:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    agent_id = str(inputs.get("agent_id") or payload.get("agent_id") or "")
    query = str(inputs.get("query") or payload.get("query") or "Run workflow")
    request = AgentRunRequest(
        agent_id=agent_id,
        input=AgentRunInput(messages=[{"role": "user", "content": query}]),
        user=str(payload.get("user")) if payload.get("user") is not None else None,
        metadata=inputs,
    )
    if str(payload.get("response_mode") or "blocking") == "streaming":
        return await run_stream(request, db, principal)
    run = await run_wait(request, db, principal)
    return {
        "workflow_run_id": run.id,
        "task_id": run.id,
        "data": {
            "id": run.id,
            "status": run.status,
            "outputs": run.output or {},
        },
    }


@router.post("/chat/completions", response_model=None)
async def openai_chat_completions(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> dict[str, Any] | StreamingResponse:
    model = str(payload.get("model") or "")
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    request = AgentRunRequest(
        agent_id=model,
        input=AgentRunInput(messages=messages),
        metadata={"compat": "openai"},
    )
    if bool(payload.get("stream")):
        return await _run_openai_stream_response(
            request=request,
            db=db,
            principal=principal,
            model=model,
        )
    run = await run_wait(request, db, principal)
    answer = (run.output or {}).get("answer", "")
    return {
        "id": run.id,
        "object": "chat.completion",
        "created": int(run.created_at.timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
