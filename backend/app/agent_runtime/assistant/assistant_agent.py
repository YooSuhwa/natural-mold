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


async def _resolve_system_api_key(
    db: AsyncSession, provider: str
) -> str | None:
    """Operator-key resolution for the Assistant model.

    Tiered:
      1. ENV (PROVIDER_API_KEY_MAP) — bootstrap convenience.
      2. ``Credential`` row with ``is_system=True`` matching ``provider`` —
         operator manages keys via the System Credentials page instead of
         editing .env on the server.
      3. ``None`` — caller surfaces the resulting LLM error.

    Notes:
      - User credentials are intentionally NOT consulted. System functions
        (Fix Agent / builder / image generation) bill the operator, not
        whichever user happens to be logged in.
    """

    env_key = PROVIDER_API_KEY_MAP.get(provider)
    if env_key:
        return env_key

    cred = await credential_service.find_system_by_definition(db, provider)
    if cred is None:
        return None
    try:
        payload = await credential_service.decrypt_with_external(
            cred.data_encrypted
        )
    except Exception:  # noqa: BLE001
        logger.exception("System credential %s decryption failed", cred.id)
        return None
    api_key = payload.get("api_key") or payload.get("token")
    return str(api_key) if api_key else None


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
    api_key = await _resolve_system_api_key(
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
