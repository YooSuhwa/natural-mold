"""장기 기억 컨텍스트 로딩 + 회상 브리프 + 쓰기 정책 (BE-S10 분리).

주의: ``memory_service``/DB 세션 함수-로컬 import 는 기존 코드 그대로 유지한다
— 인자 주입으로의 역전은 BE-S4(Stage 5) 작업이며 여기서는 순수 이동만 한다.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any

from app.agent_runtime.runtime_config import AgentConfig

logger = logging.getLogger(__name__)


def _parse_uuid(value: str | None) -> _uuid.UUID | None:
    if not value:
        return None
    try:
        return _uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


# 회상 칩 payload에 싣는 기억 내용 미리보기 상한 — 전문은 memory 설정 화면에서
# 확인하고, 스트림 이벤트에는 식별 가능한 한 줄만 싣는다.
_RECALLED_MEMORY_PREVIEW_CHARS = 200


def _recalled_memory_briefs(records: list[Any]) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for record in records:
        content = str(record.content or "").strip()
        if len(content) > _RECALLED_MEMORY_PREVIEW_CHARS:
            content = content[:_RECALLED_MEMORY_PREVIEW_CHARS] + "…"
        briefs.append(
            {
                "id": str(record.id),
                "scope": record.scope,
                "content": content,
            }
        )
    return briefs


async def _load_memory_context(cfg: AgentConfig) -> tuple[str, list[dict[str, Any]]]:
    """Load the long-term memory prompt block plus recall briefs.

    Returns ``(prompt, briefs)`` — ``briefs`` feed the ``moldy.memory_recalled``
    stream-head event so the chat can show which memories informed this run.
    """

    user_uuid = _parse_uuid(cfg.user_id)
    if user_uuid is None:
        return "", []
    agent_uuid = _parse_uuid(cfg.agent_id)
    try:
        from app.database import async_session as _async_session_factory
        from app.services import memory_service

        async with _async_session_factory() as db:
            policy = await memory_service.resolve_effective_policy(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
            )
            if not policy.read_enabled:
                return "", []
            records = await memory_service.list_runtime_memory_records(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
                allowed_scopes=policy.allowed_scopes,
            )
            prompt = memory_service.render_memory_prompt(records)
            return prompt, _recalled_memory_briefs(records) if prompt else []
    except Exception:  # noqa: BLE001 — memory is helpful context, not a hard runtime dependency
        logger.warning("memory prompt load failed", exc_info=True)
        return "", []


async def _memory_write_policy_for_run(cfg: AgentConfig, *, is_trigger_mode: bool) -> str:
    user_uuid = _parse_uuid(cfg.user_id)
    if user_uuid is None:
        return "off"
    agent_uuid = _parse_uuid(cfg.agent_id)
    try:
        from app.database import async_session as _async_session_factory
        from app.services import memory_service

        async with _async_session_factory() as db:
            policy = await memory_service.resolve_effective_policy(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
            )
            return policy.trigger_write_policy if is_trigger_mode else policy.write_policy
    except Exception:  # noqa: BLE001 — memory writes are optional runtime affordances
        logger.warning("memory write policy load failed", exc_info=True)
        return "off"
