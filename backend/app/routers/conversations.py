from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
)
from app.services import chat_service
from app.agent_runtime.executor import execute_agent_stream

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
        raise HTTPException(status_code=404, detail="Agent not found")
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
        raise HTTPException(status_code=404, detail="Agent not found")
    return await chat_service.create_conversation(db, agent_id, data.title)


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
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await chat_service.list_messages(db, conversation_id)


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await chat_service.save_message(db, conversation_id, "user", data.content)

    agent = await chat_service.get_agent_with_tools(db, conv.agent_id, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    messages = await chat_service.list_messages(db, conversation_id)
    messages_history = [{"role": m.role, "content": m.content} for m in messages]

    skill_contents = chat_service.get_agent_skill_contents(agent)
    effective_prompt = agent.system_prompt
    if skill_contents:
        skills_text = "\n\n---\n\n".join(skill_contents)
        effective_prompt = f"{agent.system_prompt}\n\n## 연결된 스킬\n\n{skills_text}"

    tools_config = []
    for link in agent.tool_links:
        tool = link.tool
        # Merge: tool-level auth_config + agent-level config override
        merged_auth = {**(tool.auth_config or {}), **(link.config or {})}
        tools_config.append({
            "type": tool.type,
            "name": tool.name,
            "description": tool.description,
            "api_url": tool.api_url,
            "http_method": tool.http_method,
            "parameters_schema": tool.parameters_schema,
            "auth_type": tool.auth_type,
            "auth_config": merged_auth or None,
        })

    async def generate():
        import json

        full_content = ""
        async for chunk in execute_agent_stream(
            provider=agent.model.provider,
            model_name=agent.model.model_name,
            api_key=agent.model.api_key_encrypted,
            base_url=agent.model.base_url,
            system_prompt=effective_prompt,
            tools_config=tools_config,
            messages_history=messages_history,
            thread_id=str(conversation_id),
            model_params=agent.model_params,
        ):
            yield chunk
            if "message_end" in chunk:
                try:
                    data_line = chunk.split("data: ", 1)[1].strip()
                    end_data = json.loads(data_line)
                    full_content = end_data.get("content", "")
                except (IndexError, json.JSONDecodeError):
                    pass

        if full_content:
            await chat_service.save_message(db, conversation_id, "assistant", full_content)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
