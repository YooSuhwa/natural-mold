from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import execute_agent_stream
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db
from app.exceptions import NotFoundError
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
)
from app.services import chat_service
from app.services.encryption import decrypt_api_key

router = APIRouter(tags=["conversations"])


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
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
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
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
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
        raise NotFoundError("CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다")
    return await chat_service.update_conversation(db, conv, data)


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise NotFoundError("CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다")
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
        raise NotFoundError("CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다")
    return await chat_service.list_messages_from_checkpointer(conversation_id, conv.created_at)


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise NotFoundError("CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다")

    await chat_service.maybe_set_auto_title(db, conversation_id, data.content)

    agent = await chat_service.get_agent_with_tools(db, conv.agent_id, user.id)
    if not agent:
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")

    effective_prompt = chat_service.build_effective_prompt(agent)
    tools_config = chat_service.build_tools_config(agent, conversation_id=str(conversation_id))
    agent_skills = chat_service.build_agent_skills(agent)

    # Resolve API key: prefer llm_provider, fallback to model-level key
    lp = agent.model.llm_provider
    api_key = (
        decrypt_api_key(lp.api_key_encrypted)
        if lp and lp.api_key_encrypted
        else decrypt_api_key(agent.model.api_key_encrypted)
        if agent.model.api_key_encrypted
        else None
    )
    base_url = lp.base_url if lp and lp.base_url else agent.model.base_url

    async def generate():
        async for chunk in execute_agent_stream(
            provider=agent.model.provider,
            model_name=agent.model.model_name,
            api_key=api_key,
            base_url=base_url,
            system_prompt=effective_prompt,
            tools_config=tools_config,
            messages_history=[{"role": "user", "content": data.content}],
            thread_id=str(conversation_id),
            model_params=agent.model_params,
            middleware_configs=agent.middleware_configs,
            agent_skills=agent_skills or None,
            agent_id=str(agent.id),
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/conversations/{conversation_id}/files/{file_path:path}")
async def get_conversation_file(
    conversation_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise NotFoundError("CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다")

    base = Path(settings.conversation_output_dir) / str(conversation_id)
    target = (base / file_path).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        raise NotFoundError("FILE_NOT_FOUND", "파일을 찾을 수 없습니다")
    return FileResponse(target)
