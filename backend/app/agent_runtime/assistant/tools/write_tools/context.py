"""Assistant 쓰기 도구 — 공유 컨텍스트.

클로저 캡처(agent_id/user_id/async_session_factory) 대신 명시적 객체로 전달한다.
`session_factory`는 `build_write_tools`가 호출 시점에 패키지 전역
`async_session_factory`를 읽어 주입한다 (테스트 monkeypatch 표면 유지 —
그룹 모듈이 `async_session_factory`를 직접 import하면 patch를 우회하므로 금지).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.models.agent import Agent


@dataclass(frozen=True)
class WriteToolContext:
    """쓰기 도구 그룹 빌더가 공유하는 실행 컨텍스트."""

    session_factory: Callable[[], AsyncSession]
    agent_id: uuid.UUID
    user_id: uuid.UUID


async def get_agent_with_session(
    ctx: WriteToolContext,
    session: AsyncSession,
) -> Agent | None:
    return await get_agent_with_eager_load(session, ctx.agent_id, ctx.user_id)
