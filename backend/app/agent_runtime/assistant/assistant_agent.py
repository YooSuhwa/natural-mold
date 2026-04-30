"""Assistant v2 에이전트 — build_agent + 35개 도구 바인딩.

assistant/prompt.md를 시스템 프롬프트로 로드하고,
read/write/clarify 도구를 바인딩한다.
"""

from __future__ import annotations

import functools
import logging
import uuid
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.clarify_tools import build_clarify_tools
from app.agent_runtime.assistant.tools.read_tools import build_read_tools
from app.agent_runtime.assistant.tools.write_tools import build_write_tools
from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.executor import build_agent
from app.agent_runtime.model_factory import PROVIDER_API_KEY_MAP, create_chat_model
from app.config import settings
from app.credentials import service as credential_service
from app.services.system_credential_resolver import resolve_system_api_key

logger = logging.getLogger(__name__)

# Assistant 시스템 프롬프트 파일 경로
# __file__ = backend/app/agent_runtime/assistant/assistant_agent.py
# .parent = assistant/ (prompt.md와 같은 디렉토리)
_PROMPT_PATH = Path(__file__).resolve().parent / "prompt.md"


@functools.cache
def _load_system_prompt() -> str:
    """Assistant 시스템 프롬프트를 파일에서 로드한다 (캐시됨)."""
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Assistant prompt file not found: %s, using fallback", _PROMPT_PATH)
        return (
            "You are Moldy Agent Assistant, an AI that modifies existing "
            "agent configurations. Always VERIFY before MODIFY."
        )


# ``_resolve_system_api_key`` was promoted to
# ``app.services.system_credential_resolver.resolve_system_api_key`` so the
# image generation flows can share the same ENV → system-credential
# fallback policy. Re-exported under the legacy name for the test that
# patches it via ``patch.object``.
_resolve_system_api_key = resolve_system_api_key


async def build_assistant_agent(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: str,
) -> Any:
    """Assistant 에이전트를 생성한다.

    Args:
        db: DB 세션 (도구가 DB에 직접 접근)
        agent_id: 대상 에이전트 ID
        user_id: 사용자 ID
        thread_id: 대화 스레드 ID (checkpointer용)

    Returns:
        CompiledStateGraph — build_agent의 반환값
    """
    api_key = await resolve_system_api_key(
        db, settings.assistant_model_provider
    )
    model: BaseChatModel = create_chat_model(
        settings.assistant_model_provider,
        settings.assistant_model_name,
        api_key=api_key,
    )

    # 도구 35개 = 16 read + 18 write + 1 clarify
    tools = (
        build_read_tools(db, agent_id, user_id)
        + build_write_tools(db, agent_id, user_id)
        + build_clarify_tools()
    )

    system_prompt = _load_system_prompt()

    return build_agent(
        model=model,
        tools=tools,  # type: ignore[arg-type]  # StructuredTool은 BaseTool 호환 (langchain runtime 동작 OK)
        system_prompt=system_prompt,
        middleware=[],
        checkpointer=get_checkpointer(),
        name=f"assistant_{str(agent_id)[:8]}",
    )
