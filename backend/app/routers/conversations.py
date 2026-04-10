from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import execute_agent_stream, resume_agent_stream
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import agent_not_found, conversation_not_found, file_not_found
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
    ResumeRequest,
)
from app.services import chat_service
from app.services.encryption import decrypt_api_key
from app.services.provider_service import load_all_provider_api_keys

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_agent_context(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user: CurrentUser,
) -> dict[str, Any]:
    """conversation + agent 조회 + API 키/도구/미들웨어/비용 해석.

    send_message와 resume_message에서 공통으로 사용.
    """
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    agent = await chat_service.get_agent_with_tools(db, conv.agent_id, user.id)
    if not agent:
        raise agent_not_found()

    lp = agent.model.llm_provider
    api_key = (
        decrypt_api_key(lp.api_key_encrypted)
        if lp and lp.api_key_encrypted
        else decrypt_api_key(agent.model.api_key_encrypted)
        if agent.model.api_key_encrypted
        else None
    )
    base_url = lp.base_url if lp and lp.base_url else agent.model.base_url

    return {
        "conversation": conv,
        "agent": agent,
        "provider": agent.model.provider,
        "model_name": agent.model.model_name,
        "api_key": api_key,
        "base_url": base_url,
        "system_prompt": chat_service.build_effective_prompt(agent),
        "tools_config": chat_service.build_tools_config(
            agent, conversation_id=str(conversation_id)
        ),
        "model_params": agent.model_params,
        "middleware_configs": agent.middleware_configs,
        "agent_skills": chat_service.build_agent_skills(agent) or None,
        "agent_id": str(agent.id),
        "cost_per_input_token": (
            float(agent.model.cost_per_input_token)
            if agent.model.cost_per_input_token
            else None
        ),
        "cost_per_output_token": (
            float(agent.model.cost_per_output_token)
            if agent.model.cost_per_output_token
            else None
        ),
        "provider_api_keys": await load_all_provider_api_keys(db),
    }


def _error_sse_pair(error_message: str) -> list[str]:
    """에러 SSE + message_end 페어를 생성."""
    from app.agent_runtime.streaming import format_sse

    return [
        format_sse("error", {"message": error_message}),
        format_sse("message_end", {"usage": {}, "content": ""}),
    ]


def _sse_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    """StreamingResponse 래퍼."""  # noqa: D401
    return StreamingResponse(generator, media_type="text/event-stream", headers=_SSE_HEADERS)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/api/agents/{agent_id}/conversations",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    return await chat_service.list_conversations(db, agent_id)


@router.post(
    "/api/agents/{agent_id}/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    agent_id: uuid.UUID,
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    return await chat_service.create_conversation(db, agent_id, data.title)


@router.patch(
    "/api/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
async def update_conversation(
    conversation_id: uuid.UUID,
    data: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    return await chat_service.update_conversation(db, conv, data)


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    await chat_service.delete_conversation(db, conv)


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    return await chat_service.list_messages_from_checkpointer(conversation_id, conv.created_at)


# ---------------------------------------------------------------------------
# Streaming: send + resume
# ---------------------------------------------------------------------------


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ctx = await _resolve_agent_context(db, conversation_id, user)
    await chat_service.maybe_set_auto_title(db, conversation_id, data.content)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            async for chunk in execute_agent_stream(
                provider=ctx["provider"],
                model_name=ctx["model_name"],
                api_key=ctx["api_key"],
                base_url=ctx["base_url"],
                system_prompt=ctx["system_prompt"],
                tools_config=ctx["tools_config"],
                messages_history=[{"role": "user", "content": data.content}],
                thread_id=str(conversation_id),
                model_params=ctx["model_params"],
                middleware_configs=ctx["middleware_configs"],
                agent_skills=ctx["agent_skills"],
                agent_id=ctx["agent_id"],
                cost_per_input_token=ctx["cost_per_input_token"],
                cost_per_output_token=ctx["cost_per_output_token"],
                provider_api_keys=ctx["provider_api_keys"],
            ):
                yield chunk
        except Exception:
            logger.exception("Agent stream failed for conversation %s", conversation_id)
            for chunk in _error_sse_pair("에이전트 실행 중 오류가 발생했습니다."):
                yield chunk

    return _sse_response(generate())


@router.post("/api/conversations/{conversation_id}/messages/resume")
async def resume_message(
    conversation_id: uuid.UUID,
    data: ResumeRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """HiTL interrupt 재개 — Command(resume=response)로 그래프 실행 재개."""
    ctx = await _resolve_agent_context(db, conversation_id, user)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            async for chunk in resume_agent_stream(
                provider=ctx["provider"],
                model_name=ctx["model_name"],
                api_key=ctx["api_key"],
                base_url=ctx["base_url"],
                system_prompt=ctx["system_prompt"],
                tools_config=ctx["tools_config"],
                thread_id=str(conversation_id),
                resume_value=data.response,
                model_params=ctx["model_params"],
                middleware_configs=ctx["middleware_configs"],
                agent_skills=ctx["agent_skills"],
                agent_id=ctx["agent_id"],
                cost_per_input_token=ctx["cost_per_input_token"],
                cost_per_output_token=ctx["cost_per_output_token"],
                provider_api_keys=ctx["provider_api_keys"],
            ):
                yield chunk
        except Exception:
            logger.exception("Agent resume failed for conversation %s", conversation_id)
            for chunk in _error_sse_pair("에이전트 재개 중 오류가 발생했습니다."):
                yield chunk

    return _sse_response(generate())


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------


@router.get("/api/conversations/{conversation_id}/files/{file_path:path}")
async def get_conversation_file(
    conversation_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    base = Path(settings.conversation_output_dir) / str(conversation_id)
    target = (base / file_path).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        raise file_not_found()
    return FileResponse(target)
