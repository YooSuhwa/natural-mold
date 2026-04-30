from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import AgentConfig, execute_agent_stream, resume_agent_stream
from app.agent_runtime.model_factory import env_provider_keys
from app.config import settings
from app.credentials import service as credential_service
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import agent_not_found, conversation_not_found, file_not_found
from app.models.model import Model
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
    ResumeRequest,
)
from app.services import chat_service

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
) -> AgentConfig:
    """conversation + agent 조회 → AgentConfig 생성.

    send_message와 resume_message에서 공통으로 사용.
    """
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    agent = await chat_service.get_agent_with_tools(db, conv.agent_id, user.id)
    if not agent:
        raise agent_not_found()

    api_key: str | None = None
    if agent.llm_credential is not None:
        try:
            payload = await credential_service.decrypt_with_external(
                agent.llm_credential.data_encrypted
            )
            api_key = payload.get("api_key") or payload.get("token")
        except Exception:  # noqa: BLE001 — fall through to env-var fallback
            logger.exception(
                "LLM credential %s decryption failed for agent %s",
                agent.llm_credential.id,
                agent.id,
            )
    base_url = agent.model.base_url

    tools_config = await chat_service.build_tools_config(
        agent, db=db, conversation_id=str(conversation_id)
    )

    fallback_chain = await _resolve_fallback_chain(db, agent.model_fallback_list)

    return AgentConfig(
        provider=agent.model.provider,
        model_name=agent.model.model_name,
        api_key=api_key,
        base_url=base_url,
        system_prompt=chat_service.build_effective_prompt(agent),
        tools_config=tools_config,
        thread_id=str(conversation_id),
        model_params=agent.model_params,
        middleware_configs=agent.middleware_configs,
        agent_skills=chat_service.build_agent_skills(agent) or None,
        agent_id=str(agent.id),
        provider_api_keys=env_provider_keys(),
        cost_per_input_token=(
            float(agent.model.cost_per_input_token) if agent.model.cost_per_input_token else None
        ),
        cost_per_output_token=(
            float(agent.model.cost_per_output_token) if agent.model.cost_per_output_token else None
        ),
        user_id=str(agent.user_id),
        model_id=str(agent.model.id) if agent.model else None,
        llm_credential_id=(
            str(agent.llm_credential.id) if agent.llm_credential is not None else None
        ),
        model_fallback_chain=fallback_chain,
    )


async def _resolve_fallback_chain(
    db: AsyncSession,
    fallback_list: list[str] | None,
) -> list[dict[str, str | None]] | None:
    """Resolve agent.model_fallback_list (UUID strings) → ordered chain dicts.

    Missing rows are silently dropped — the catalog can change while an
    agent's fallback list is stale, and we don't want a deleted fallback
    breaking the runtime. Returns ``None`` when there are no resolvable
    entries so the executor skips the fallback path entirely.
    """

    if not fallback_list:
        return None
    from sqlalchemy import select

    fallback_uuids: list[uuid.UUID] = []
    for raw in fallback_list:
        try:
            fallback_uuids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not fallback_uuids:
        return None
    result = await db.execute(select(Model).where(Model.id.in_(fallback_uuids)))
    rows = {row.id: row for row in result.scalars().all()}
    chain: list[dict[str, str | None]] = []
    for fid in fallback_uuids:
        row = rows.get(fid)
        if row is None:
            continue
        chain.append(
            {
                "provider": row.provider,
                "model_name": row.model_name,
                "base_url": row.base_url,
                "model_id": str(row.id),
            }
        )
    return chain or None


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
    return await chat_service.list_messages_from_checkpointer(db, conv)


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
    cfg = await _resolve_agent_context(db, conversation_id, user)
    await chat_service.maybe_set_auto_title(db, conversation_id, data.content)
    # 메시지 송신 시점에 conv.updated_at 갱신 → list_messages refetch에서 정확한 base.
    # generate() 안에서 호출하면 SSE 응답 후 db session이 close되어 실패 가능.
    await chat_service.touch_conversation(db, conversation_id)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            async for chunk in execute_agent_stream(
                cfg,
                [{"role": "user", "content": data.content}],
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
    cfg = await _resolve_agent_context(db, conversation_id, user)
    await chat_service.touch_conversation(db, conversation_id)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            async for chunk in resume_agent_stream(cfg, data.response):
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
