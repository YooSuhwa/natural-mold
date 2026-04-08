"""Assistant v2 서비스 — 대화 관리, SSE 스트리밍."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.assistant_agent import build_assistant_agent
from app.agent_runtime.streaming import stream_agent_response

logger = logging.getLogger(__name__)


async def stream_assistant_message(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """Assistant 메시지를 SSE 스트리밍으로 처리한다.

    기존 채팅 인프라(stream_agent_response)를 그대로 재사용한다 (ADR-005 AD-6).
    checkpointer가 히스토리를 자동 관리하므로 별도 세션 테이블 불필요.
    """
    agent = build_assistant_agent(db, agent_id, user_id, thread_id)

    messages = [HumanMessage(content=user_message)]
    config = {"configurable": {"thread_id": thread_id}}

    async for chunk in stream_agent_response(agent, messages, config):
        yield chunk
