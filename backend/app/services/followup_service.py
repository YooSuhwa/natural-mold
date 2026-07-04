"""Follow-up suggestion generation (composer ghost text).

Generates ONE short follow-up prompt the user is likely to send next, from the
tail of the conversation, using the operator-selected system LLM (ADR-019
``text_primary``). Fail-safe by design: any failure — unconfigured system
model, provider error, empty output — returns ``None`` and the frontend simply
doesn't show the ghost. This must never block or degrade the chat itself.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation
from app.services import chat_service
from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    resolve_system_model,
)

logger = logging.getLogger(__name__)

# E2E scripted 배포에선 system credential 없이도 파이프라인 전체(엔드포인트 →
# 훅 → 고스트 → 수락)를 결정적으로 검증할 수 있도록 고정 제안을 돌려준다.
E2E_FOLLOWUP_SUGGESTION = "방금 답변을 표로 정리해줘"

_MAX_TAIL_MESSAGES = 6
_MAX_MESSAGE_CHARS = 1500
_MAX_SUGGESTION_CHARS = 120

_SYSTEM_PROMPT = (
    "너는 채팅 입력창의 자동 제안 기능이다. 대화 기록을 보고 사용자가 다음에 "
    "보낼 법한 후속 요청을 정확히 한 문장, 한국어로 제안한다.\n"
    "- 따옴표·번호·불릿 없이 제안 문장만 출력한다.\n"
    "- 60자 이내로 짧고 실행 가능한 요청이어야 한다.\n"
    "- 방금 어시스턴트가 한 작업을 자연스럽게 잇는 요청이어야 한다."
)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _sanitize_suggestion(raw: str) -> str | None:
    """모델 출력 → 제안 한 줄. 불릿/따옴표/코드펜스 장식 제거 + 길이 상한."""

    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("```"):
            continue
        text = text.lstrip("-*•").strip()
        # "1. " / "1) " 류 번호 접두 제거.
        head = text.split(" ", 1)
        if len(head) == 2 and head[0].rstrip(".)").isdigit():
            text = head[1].strip()
        text = text.strip('"“”‘’`').strip()
        if not text:
            continue
        if len(text) > _MAX_SUGGESTION_CHARS:
            text = text[:_MAX_SUGGESTION_CHARS].rstrip()
        return text
    return None


def _transcript_tail(messages: list[Any]) -> str | None:
    tail = [
        message
        for message in messages
        if getattr(message, "role", None) in {"user", "assistant"}
        and isinstance(getattr(message, "content", None), str)
        and message.content.strip()
    ][-_MAX_TAIL_MESSAGES:]
    if not tail:
        return None
    lines = []
    for message in tail:
        speaker = "사용자" if message.role == "user" else "어시스턴트"
        lines.append(f"{speaker}: {message.content[:_MAX_MESSAGE_CHARS]}")
    return "\n".join(lines)


async def generate_followup_suggestion(
    db: AsyncSession,
    conversation: Conversation,
    user_id: uuid.UUID,
) -> str | None:
    """Return ONE follow-up suggestion for the conversation tail, or ``None``."""

    if settings.e2e_scripted_model_enabled:
        return E2E_FOLLOWUP_SUGGESTION

    try:
        resolved = await resolve_system_model(db, "text_primary")
    except SystemModelNotConfiguredError:
        return None

    try:
        messages = await chat_service.list_messages_from_checkpointer(db, conversation, user_id)
        transcript = _transcript_tail(messages)
        if transcript is None:
            return None

        from langchain_core.messages import HumanMessage, SystemMessage

        from app.agent_runtime.model_factory import create_chat_model

        model = create_chat_model(
            resolved.provider,
            resolved.model_name,
            resolved.api_key,
            resolved.base_url,
            allow_env_fallback=False,
        )
        result = await model.ainvoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=f"대화 기록:\n{transcript}\n\n후속 요청 제안:"),
            ]
        )
        return _sanitize_suggestion(_content_text(getattr(result, "content", "")))
    except Exception:  # noqa: BLE001 — 제안은 nice-to-have; 채팅을 막지 않는다.
        logger.warning("followup suggestion generation failed", exc_info=True)
        return None
