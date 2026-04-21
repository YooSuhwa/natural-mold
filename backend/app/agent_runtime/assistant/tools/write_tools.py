"""Assistant 쓰기 도구 — 17 도구 (DB 수정, Verify First).

도구 목록:
1. add_tool_to_agent (배치)
2. remove_tool_from_agent (배치)
3. add_middleware_to_agent (배치)
4. remove_middleware_from_agent (배치)
5. add_subagent_to_agent (배치) — PoC stub
6. remove_subagent_from_agent (배치) — PoC stub
7. edit_system_prompt (부분 수정)
8. update_system_prompt (전체 교체)
9. update_model_config
10. update_middleware_config
11. update_chat_openers
12. update_recursion_limit
13. create_cron_schedule
14. update_cron_schedule
15. delete_cron_schedule
16. enable_cron_schedule
17. disable_cron_schedule
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY
from app.database import async_session as async_session_factory
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.tool import AgentToolLink, Tool
from app.services.model_service import resolve_model


def build_write_tools(
    db: AsyncSession,  # noqa: ARG001 — kept for interface compatibility
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[StructuredTool]:
    """Assistant 쓰기 도구 18개를 생성한다.

    각 도구는 호출 시마다 fresh DB 세션을 생성하여 사용한다.
    LangGraph 에이전트의 도구 실행은 빌드 시점의 클로저 DB 세션이
    이미 닫혀 있을 수 있으므로, 매 호출마다 새 세션을 열어야 안전하다.
    """

    async def _get_agent_with_session(
        session: AsyncSession,
    ) -> Agent | None:
        return await get_agent_with_eager_load(session, agent_id, user_id)

    # ------ 1. add_tool_to_agent ------

    async def add_tool_to_agent(tool_names: list[str]) -> str:
        """에이전트에 도구를 추가합니다 (배치 지원).

        Args:
            tool_names: 추가할 도구 이름 목록
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing = {link.tool.name.lower() for link in agent.tool_links}
            lower_names = [n.lower() for n in tool_names if n.lower() not in existing]
            if not lower_names:
                return "모든 도구가 이미 추가되어 있습니다."

            result = await session.execute(
                select(Tool).where(
                    or_(Tool.user_id == user_id, Tool.is_system.is_(True)),
                    func.lower(Tool.name).in_(lower_names),
                )
            )
            found_tools = list(result.scalars().all())
            if not found_tools:
                return f"도구를 찾을 수 없습니다: {', '.join(tool_names)}"

            for t in found_tools:
                agent.tool_links.append(AgentToolLink(tool_id=t.id))
            await session.commit()

            added = [t.name for t in found_tools]
            return f"도구 추가 완료: {', '.join(added)}"

    # ------ 2. remove_tool_from_agent ------

    async def remove_tool_from_agent(tool_names: list[str]) -> str:
        """에이전트에서 도구를 제거합니다 (배치 지원).

        Args:
            tool_names: 제거할 도구 이름 목록
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            lower_names = {n.lower() for n in tool_names}
            removed = []
            for link in agent.tool_links:
                if link.tool.name.lower() in lower_names:
                    removed.append(link.tool.name)
                    await session.delete(link)
            if not removed:
                return f"해당 도구가 에이전트에 없습니다: {', '.join(tool_names)}"

            await session.commit()
            return f"도구 제거 완료: {', '.join(removed)}"

    # ------ 3. add_middleware_to_agent ------

    async def add_middleware_to_agent(middleware_names: list[str]) -> str:
        """에이전트에 미들웨어를 추가합니다 (배치 지원).

        Args:
            middleware_names: 추가할 미들웨어 type 키 목록
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            configs = list(agent.middleware_configs or [])
            existing = {mc.get("type", "").lower() for mc in configs}
            added = []
            for name in middleware_names:
                if name.lower() in existing:
                    continue
                if name not in MIDDLEWARE_REGISTRY:
                    continue
                configs.append({"type": name, "params": {}})
                added.append(name)

            if not added:
                return "추가할 미들웨어가 없습니다 (이미 존재하거나 카탈로그에 없음)."

            agent.middleware_configs = configs
            await session.commit()
            return f"미들웨어 추가 완료: {', '.join(added)}"

    # ------ 4. remove_middleware_from_agent ------

    async def remove_middleware_from_agent(middleware_names: list[str]) -> str:
        """에이전트에서 미들웨어를 제거합니다 (배치 지원).

        Args:
            middleware_names: 제거할 미들웨어 type 키 목록
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            lower_names = {n.lower() for n in middleware_names}
            configs = list(agent.middleware_configs or [])
            removed = []
            new_configs = []
            for mc in configs:
                if mc.get("type", "").lower() in lower_names:
                    removed.append(mc.get("type", ""))
                else:
                    new_configs.append(mc)

            if not removed:
                return f"해당 미들웨어가 없습니다: {', '.join(middleware_names)}"

            agent.middleware_configs = new_configs
            await session.commit()
            return f"미들웨어 제거 완료: {', '.join(removed)}"

    # ------ 5, 6. subagent stubs ------

    async def add_subagent_to_agent(agent_ids: list[str]) -> str:
        """에이전트에 서브에이전트를 추가합니다.

        Args:
            agent_ids: 추가할 서브에이전트 ID 목록
        """
        return "서브에이전트 기능은 아직 구현되지 않았습니다."

    async def remove_subagent_from_agent(agent_ids: list[str]) -> str:
        """에이전트에서 서브에이전트를 제거합니다.

        Args:
            agent_ids: 제거할 서브에이전트 ID 목록
        """
        return "서브에이전트 기능은 아직 구현되지 않았습니다."

    # ------ 7. edit_system_prompt ------

    async def edit_system_prompt(
        old_string: str, new_string: str, replace_all: bool = False
    ) -> str:
        """시스템 프롬프트의 일부를 수정합니다 (부분 교체).

        Args:
            old_string: 교체할 기존 텍스트
            new_string: 새 텍스트 (빈 문자열이면 삭제)
            replace_all: True면 모든 매치를 교체, False면 첫 번째만
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            prompt = agent.system_prompt or ""
            if old_string not in prompt:
                return f"'{old_string}'을(를) 시스템 프롬프트에서 찾을 수 없습니다."

            count = prompt.count(old_string)
            if count > 1 and not replace_all:
                return (
                    f"'{old_string}'이(가) {count}곳에서 발견되었습니다. "
                    "replace_all=true로 설정하거나 더 구체적인 텍스트를 지정해주세요."
                )

            if replace_all:
                new_prompt = prompt.replace(old_string, new_string)
            else:
                new_prompt = prompt.replace(old_string, new_string, 1)

            agent.system_prompt = new_prompt
            await session.commit()
            return "시스템 프롬프트 수정 완료."

    # ------ 8. update_system_prompt ------

    async def update_system_prompt(new_system_prompt: str) -> str:
        """시스템 프롬프트를 전체 교체합니다.

        Args:
            new_system_prompt: 새 시스템 프롬프트 전체 내용
        """
        if not new_system_prompt.strip():
            return (
                "경고: 빈 시스템 프롬프트를 설정하려고 합니다. "
                "에이전트 동작에 영향을 줄 수 있습니다. "
                "정말 빈 프롬프트를 원한다면 공백 1칸을 입력하세요."
            )
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            agent.system_prompt = new_system_prompt
            await session.commit()
            return "시스템 프롬프트 전체 교체 완료."

    # ------ 9. update_model_config ------

    async def update_model_config(
        model_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> str:
        """모델 설정을 변경합니다.

        Args:
            model_name: 새 모델 (provider:model_id 또는 display_name)
            temperature: 응답 창의성 (0.0~2.0)
            max_tokens: 최대 응답 토큰
            top_p: 누적 확률 샘플링
        """
        # W-8: 입력값 범위 검증
        if temperature is not None and not (0.0 <= temperature <= 2.0):
            return "temperature는 0.0~2.0 범위여야 합니다."
        if max_tokens is not None and max_tokens <= 0:
            return "max_tokens는 양수여야 합니다."
        if top_p is not None and not (0.0 <= top_p <= 1.0):
            return "top_p는 0.0~1.0 범위여야 합니다."

        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            changes: list[str] = []
            if model_name:
                model = await resolve_model(session, model_name, strict=True)
                if model:
                    agent.model_id = model.id
                    changes.append(f"모델: {model.display_name}")
                else:
                    return f"모델 '{model_name}'을(를) 찾을 수 없습니다."

            params = dict(agent.model_params or {})
            if temperature is not None:
                params["temperature"] = temperature
                changes.append(f"temperature: {temperature}")
            if max_tokens is not None:
                params["max_tokens"] = max_tokens
                changes.append(f"max_tokens: {max_tokens}")
            if top_p is not None:
                params["top_p"] = top_p
                changes.append(f"top_p: {top_p}")
            if params != (agent.model_params or {}):
                agent.model_params = params

            await session.commit()
            return f"모델 설정 변경 완료: {', '.join(changes)}" if changes else "변경 사항 없음."

    # ------ 11. update_middleware_config ------

    async def update_middleware_config(middleware_name: str, params: dict) -> str:
        """미들웨어의 설정 파라미터를 변경합니다.

        Args:
            middleware_name: 미들웨어 type 키
            params: 새 파라미터 딕셔너리
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            # W-7: deepcopy로 SQLAlchemy JSON mutation detection 보장
            configs = copy.deepcopy(list(agent.middleware_configs or []))
            for mc in configs:
                if mc.get("type", "").lower() == middleware_name.lower():
                    mc["params"] = {**mc.get("params", {}), **params}
                    agent.middleware_configs = configs
                    await session.commit()
                    return f"'{middleware_name}' 미들웨어 설정 변경 완료."
            return f"미들웨어 '{middleware_name}'을(를) 찾을 수 없습니다."

    # ------ 12. update_chat_openers ------

    async def update_chat_openers(openers: list[str]) -> str:
        """채팅 시작 질문을 변경합니다.

        Args:
            openers: 새 채팅 시작 질문 목록
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            params = dict(agent.model_params or {})
            params["chat_openers"] = openers
            agent.model_params = params
            await session.commit()
            return f"채팅 시작 질문 {len(openers)}개 설정 완료."

    # ------ 13. update_recursion_limit ------

    async def update_recursion_limit(limit: int) -> str:
        """재귀 한도를 변경합니다.

        Args:
            limit: 새 재귀 한도 (10~200)
        """
        if not 10 <= limit <= 200:
            return "재귀 한도는 10~200 범위여야 합니다."
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            params = dict(agent.model_params or {})
            params["recursion_limit"] = limit
            agent.model_params = params
            await session.commit()
            return f"재귀 한도를 {limit}으로 변경했습니다."

    # ------ 14. create_cron_schedule ------

    async def create_cron_schedule(
        schedule_type: str,
        message: str,
        cron_expression: str | None = None,
        scheduled_at: str | None = None,
    ) -> str:
        """크론 스케줄을 생성합니다.

        Args:
            schedule_type: "recurring" 또는 "one_time"
            message: 실행 시 전달할 메시지
            cron_expression: 반복 스케줄의 cron 표현식 (recurring일 때 필수)
            scheduled_at: 1회 실행 시점 ISO 8601 (one_time일 때 필수)
        """
        schedule_config: dict[str, Any] = {}
        if schedule_type == "recurring":
            if not cron_expression:
                return "반복 스케줄에는 cron_expression이 필요합니다."
            # W-8: cron 표현식 유효성 검증
            parts = cron_expression.strip().split()
            if len(parts) not in (5, 6):
                return (
                    f"유효하지 않은 cron 표현식입니다: '{cron_expression}'. "
                    "5~6개 필드 (분 시 일 월 요일 [초])가 필요합니다."
                )
            schedule_config = {
                "type": "cron",
                "expression": cron_expression,
                "timezone": "Asia/Seoul",
            }
        elif schedule_type == "one_time":
            if not scheduled_at:
                return "1회 스케줄에는 scheduled_at이 필요합니다."
            schedule_config = {
                "type": "one_time",
                "scheduled_at": scheduled_at,
                "timezone": "Asia/Seoul",
            }
        else:
            return "schedule_type은 'recurring' 또는 'one_time'이어야 합니다."

        async with async_session_factory() as session:
            trigger = AgentTrigger(
                agent_id=agent_id,
                user_id=user_id,
                trigger_type="cron" if schedule_type == "recurring" else "one_time",
                schedule_config=schedule_config,
                input_message=message,
            )
            session.add(trigger)
            await session.commit()
            await session.refresh(trigger)
            return f"스케줄 생성 완료 (ID: {trigger.id})"

    # ------ 15. update_cron_schedule ------

    async def update_cron_schedule(
        schedule_id: str,
        cron_expression: str | None = None,
        message: str | None = None,
    ) -> str:
        """크론 스케줄을 수정합니다.

        Args:
            schedule_id: 스케줄 UUID
            cron_expression: 새 cron 표현식
            message: 새 실행 메시지
        """
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            return "유효하지 않은 스케줄 ID입니다."
        async with async_session_factory() as session:
            result = await session.execute(
                select(AgentTrigger).where(
                    AgentTrigger.id == sid, AgentTrigger.agent_id == agent_id
                )
            )
            trigger = result.scalar_one_or_none()
            if not trigger:
                return "스케줄을 찾을 수 없습니다."

            if cron_expression:
                config = dict(trigger.schedule_config)
                config["expression"] = cron_expression
                trigger.schedule_config = config
            if message:
                trigger.input_message = message
            await session.commit()
            return "스케줄 수정 완료."

    # ------ 16. delete_cron_schedule ------

    async def delete_cron_schedule(schedule_id: str) -> str:
        """크론 스케줄을 삭제합니다.

        Args:
            schedule_id: 스케줄 UUID
        """
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            return "유효하지 않은 스케줄 ID입니다."
        async with async_session_factory() as session:
            result = await session.execute(
                select(AgentTrigger).where(
                    AgentTrigger.id == sid, AgentTrigger.agent_id == agent_id
                )
            )
            trigger = result.scalar_one_or_none()
            if not trigger:
                return "스케줄을 찾을 수 없습니다."
            await session.delete(trigger)
            await session.commit()
            return "스케줄 삭제 완료."

    # ------ 17. enable_cron_schedule ------

    async def enable_cron_schedule(schedule_id: str) -> str:
        """크론 스케줄을 활성화합니다.

        Args:
            schedule_id: 스케줄 UUID
        """
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            return "유효하지 않은 스케줄 ID입니다."
        async with async_session_factory() as session:
            result = await session.execute(
                select(AgentTrigger).where(
                    AgentTrigger.id == sid, AgentTrigger.agent_id == agent_id
                )
            )
            trigger = result.scalar_one_or_none()
            if not trigger:
                return "스케줄을 찾을 수 없습니다."
            trigger.status = "active"
            await session.commit()
            return "스케줄 활성화 완료."

    # ------ 18. disable_cron_schedule ------

    async def disable_cron_schedule(schedule_id: str) -> str:
        """크론 스케줄을 비활성화합니다.

        Args:
            schedule_id: 스케줄 UUID
        """
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            return "유효하지 않은 스케줄 ID입니다."
        async with async_session_factory() as session:
            result = await session.execute(
                select(AgentTrigger).where(
                    AgentTrigger.id == sid, AgentTrigger.agent_id == agent_id
                )
            )
            trigger = result.scalar_one_or_none()
            if not trigger:
                return "스케줄을 찾을 수 없습니다."
            trigger.status = "paused"
            await session.commit()
            return "스케줄 비활성화 완료."

    # ------ Build tools list ------

    return [
        StructuredTool.from_function(
            coroutine=add_tool_to_agent,
            name="add_tool_to_agent",
            description="에이전트에 도구 배치 추가",
        ),
        StructuredTool.from_function(
            coroutine=remove_tool_from_agent,
            name="remove_tool_from_agent",
            description="에이전트에서 도구 배치 제거",
        ),
        StructuredTool.from_function(
            coroutine=add_middleware_to_agent,
            name="add_middleware_to_agent",
            description="에이전트에 미들웨어 배치 추가",
        ),
        StructuredTool.from_function(
            coroutine=remove_middleware_from_agent,
            name="remove_middleware_from_agent",
            description="에이전트에서 미들웨어 배치 제거",
        ),
        StructuredTool.from_function(
            coroutine=add_subagent_to_agent,
            name="add_subagent_to_agent",
            description="에이전트에 서브에이전트 추가",
        ),
        StructuredTool.from_function(
            coroutine=remove_subagent_from_agent,
            name="remove_subagent_from_agent",
            description="에이전트에서 서브에이전트 제거",
        ),
        StructuredTool.from_function(
            coroutine=edit_system_prompt,
            name="edit_system_prompt",
            description="시스템 프롬프트 부분 수정 (old_string→new_string 교체)",
        ),
        StructuredTool.from_function(
            coroutine=update_system_prompt,
            name="update_system_prompt",
            description="시스템 프롬프트 전체 교체",
        ),
        StructuredTool.from_function(
            coroutine=update_model_config,
            name="update_model_config",
            description="모델 설정 변경 (모델명, temperature, max_tokens 등)",
        ),
        StructuredTool.from_function(
            coroutine=update_middleware_config,
            name="update_middleware_config",
            description="미들웨어의 설정 파라미터 변경",
        ),
        StructuredTool.from_function(
            coroutine=update_chat_openers,
            name="update_chat_openers",
            description="채팅 시작 질문 변경",
        ),
        StructuredTool.from_function(
            coroutine=update_recursion_limit,
            name="update_recursion_limit",
            description="재귀 한도 변경 (10~200)",
        ),
        StructuredTool.from_function(
            coroutine=create_cron_schedule,
            name="create_cron_schedule",
            description="크론 스케줄 생성 (반복 또는 1회)",
        ),
        StructuredTool.from_function(
            coroutine=update_cron_schedule,
            name="update_cron_schedule",
            description="크론 스케줄 수정",
        ),
        StructuredTool.from_function(
            coroutine=delete_cron_schedule,
            name="delete_cron_schedule",
            description="크론 스케줄 삭제",
        ),
        StructuredTool.from_function(
            coroutine=enable_cron_schedule,
            name="enable_cron_schedule",
            description="크론 스케줄 활성화",
        ),
        StructuredTool.from_function(
            coroutine=disable_cron_schedule,
            name="disable_cron_schedule",
            description="크론 스케줄 비활성화",
        ),
    ]
