"""Assistant 쓰기 도구 — DB 수정 도구 (Verify First).

도구 목록:
1. add_tool_to_agent (배치)
2. remove_tool_from_agent (배치)
3. add_middleware_to_agent (배치)
4. remove_middleware_from_agent (배치)
5. add_subagent_to_agent (배치)
6. remove_subagent_from_agent (배치)
7. add_skill_to_agent (배치)
8. remove_skill_from_agent (배치)
9. edit_system_prompt (부분 수정)
10. update_system_prompt (전체 교체)
11. update_model_config
12. update_middleware_config
13. update_chat_openers
14. update_agent_metadata
15. update_agent_identity_mode
16. update_recursion_limit
17. create_cron_schedule
18. update_cron_schedule
19. delete_cron_schedule
20. enable_cron_schedule
21. disable_cron_schedule
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.agent_runtime.identity import AGENT_IDENTITY_PER_USER, validate_identity_mode
from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY
from app.database import async_session as async_session_factory
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from app.schemas.trigger import TriggerCreate, TriggerUpdate
from app.services import trigger_service
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
                    or_(Tool.user_id == user_id, Tool.user_id.is_(None)),
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

    # ------ 2-1. add_mcp_tool_to_agent ------

    async def add_mcp_tool_to_agent(mcp_tool_names: list[str]) -> str:
        """에이전트에 MCP 도구를 추가합니다 (배치 지원).

        Args:
            mcp_tool_names: 추가할 MCP 도구 이름 목록 (서버명 아님 — 도구명).
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing = {link.mcp_tool.name.lower() for link in agent.mcp_tool_links}
            lower_names = [n.lower() for n in mcp_tool_names if n.lower() not in existing]
            if not lower_names:
                return "모든 MCP 도구가 이미 추가되어 있습니다."

            # MCP 도구는 사용자 소유 server에 묶여 있음 → server.user_id 필터.
            result = await session.execute(
                select(McpTool)
                .join(McpServer, McpServer.id == McpTool.server_id)
                .where(
                    McpServer.user_id == user_id,
                    func.lower(McpTool.name).in_(lower_names),
                )
            )
            found = list(result.scalars().all())
            if not found:
                return f"MCP 도구를 찾을 수 없습니다: {', '.join(mcp_tool_names)}"

            for mt in found:
                agent.mcp_tool_links.append(AgentMcpToolLink(mcp_tool_id=mt.id))
            await session.commit()

            added = [mt.name for mt in found]
            return f"MCP 도구 추가 완료: {', '.join(added)}"

    # ------ 2-2. remove_mcp_tool_from_agent ------

    async def remove_mcp_tool_from_agent(mcp_tool_names: list[str]) -> str:
        """에이전트에서 MCP 도구를 제거합니다 (배치 지원).

        Args:
            mcp_tool_names: 제거할 MCP 도구 이름 목록.
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            lower_names = {n.lower() for n in mcp_tool_names}
            removed = []
            for link in agent.mcp_tool_links:
                if link.mcp_tool.name.lower() in lower_names:
                    removed.append(link.mcp_tool.name)
                    await session.delete(link)
            if not removed:
                return f"해당 MCP 도구가 에이전트에 없습니다: {', '.join(mcp_tool_names)}"

            await session.commit()
            return f"MCP 도구 제거 완료: {', '.join(removed)}"

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

    # ------ 5. add_subagent_to_agent ------

    async def add_subagent_to_agent(agent_ids: list[str]) -> str:
        """에이전트에 서브에이전트를 추가합니다 (배치 지원).

        Args:
            agent_ids: 추가할 서브에이전트 UUID 목록 (문자열)
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing_ids = {link.sub_agent_id for link in agent.sub_agent_links}
            added: list[str] = []
            skipped: list[str] = []

            # 1차 파싱: UUID 변환 + 자기참조/중복 즉시 분류
            candidates: dict[uuid.UUID, str] = {}  # uuid → raw_id (검증 대상)
            for raw_id in agent_ids:
                try:
                    sid = uuid.UUID(raw_id)
                except (ValueError, TypeError):
                    skipped.append(f"{raw_id}(잘못된 UUID)")
                    continue
                if sid == agent.id:
                    skipped.append(f"{raw_id}(자기 참조)")
                    continue
                if sid in existing_ids:
                    skipped.append(f"{raw_id}(이미 추가됨)")
                    continue
                candidates[sid] = raw_id

            # 2차 일괄 검증: 단일 IN 쿼리로 소유권 확인 (N+1 제거)
            name_by_id: dict[uuid.UUID, str] = {}
            if candidates:
                result = await session.execute(
                    select(Agent.id, Agent.name).where(
                        Agent.id.in_(candidates.keys()),
                        Agent.user_id == user_id,
                        # 히든 런타임 에이전트는 서브에이전트 결선 불가.
                        Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                    )
                )
                name_by_id = {row.id: row.name for row in result.all()}

            # 3차 append (검증 통과한 것만)
            next_pos = max((link.position for link in agent.sub_agent_links), default=-1) + 1
            for sid, raw_id in candidates.items():
                if sid not in name_by_id:
                    skipped.append(f"{raw_id}(찾을 수 없음)")
                    continue
                agent.sub_agent_links.append(AgentSubAgentLink(sub_agent_id=sid, position=next_pos))
                existing_ids.add(sid)
                added.append(name_by_id[sid])
                next_pos += 1

            if not added and not skipped:
                return "추가할 서브에이전트가 없습니다."

            if added:
                await session.commit()

            parts = []
            if added:
                parts.append(f"추가 완료: {', '.join(added)}")
            if skipped:
                parts.append(f"건너뜀: {', '.join(skipped)}")
            return " | ".join(parts)

    # ------ 6. remove_subagent_from_agent ------

    async def remove_subagent_from_agent(agent_ids: list[str]) -> str:
        """에이전트에서 서브에이전트를 제거합니다 (배치 지원).

        Args:
            agent_ids: 제거할 서브에이전트 UUID 목록 (문자열)
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            target_ids: set[uuid.UUID] = set()
            invalid: list[str] = []
            for raw_id in agent_ids:
                try:
                    target_ids.add(uuid.UUID(raw_id))
                except (ValueError, TypeError):
                    invalid.append(raw_id)

            removed: list[str] = []
            for link in list(agent.sub_agent_links):
                if link.sub_agent_id in target_ids:
                    removed.append(link.sub_agent.name)
                    await session.delete(link)

            if not removed:
                msg = "해당 서브에이전트가 에이전트에 없습니다."
                if invalid:
                    msg += f" (유효하지 않은 ID: {', '.join(invalid)})"
                return msg

            await session.commit()
            result_msg = f"서브에이전트 제거 완료: {', '.join(removed)}"
            if invalid:
                result_msg += f" | 유효하지 않은 ID 건너뜀: {', '.join(invalid)}"
            return result_msg

    # ------ 6-2. add_skill_to_agent ------

    async def add_skill_to_agent(skill_names: list[str]) -> str:
        """에이전트에 스킬을 추가합니다 (배치 지원).

        Args:
            skill_names: 추가할 스킬 이름 목록 (Skill.name 매칭)
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing = {link.skill.name.lower() for link in agent.skill_links}
            lower_names = [n.lower() for n in skill_names if n.lower() not in existing]
            if not lower_names:
                return "모든 스킬이 이미 추가되어 있습니다."

            result = await session.execute(
                select(Skill)
                .where(
                    Skill.user_id == user_id,
                    func.lower(Skill.name).in_(lower_names),
                )
                .order_by(Skill.created_at.asc())
            )
            # 동명 skill이 여러 개일 때 가장 먼저 생성된 1건만 채택 (per name dedupe)
            unique_by_name: dict[str, Skill] = {}
            for s in result.scalars().all():
                key = s.name.lower()
                if key not in unique_by_name:
                    unique_by_name[key] = s
            found_skills = list(unique_by_name.values())
            if not found_skills:
                return f"스킬을 찾을 수 없습니다: {', '.join(skill_names)}"

            for s in found_skills:
                agent.skill_links.append(AgentSkillLink(skill_id=s.id))
            await session.commit()

            added = [s.name for s in found_skills]
            found_lower = {s.name.lower() for s in found_skills}
            missing = [
                n for n in skill_names if n.lower() not in found_lower and n.lower() not in existing
            ]
            msg = f"스킬 추가 완료: {', '.join(added)}"
            if missing:
                msg += f" | 미존재 건너뜀: {', '.join(missing)}"
            return msg

    # ------ 6-3. remove_skill_from_agent ------

    async def remove_skill_from_agent(skill_names: list[str]) -> str:
        """에이전트에서 스킬을 제거합니다 (배치 지원).

        Args:
            skill_names: 제거할 스킬 이름 목록 (Skill.name 매칭)
        """
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            lower_names = {n.lower() for n in skill_names}
            removed: list[str] = []
            for link in list(agent.skill_links):
                if link.skill.name.lower() in lower_names:
                    removed.append(link.skill.name)
                    await session.delete(link)
            if not removed:
                return f"해당 스킬이 에이전트에 없습니다: {', '.join(skill_names)}"

            await session.commit()
            return f"스킬 제거 완료: {', '.join(removed)}"

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
        """채팅 시작 질문(오프너)을 변경합니다.

        Args:
            openers: 새 오프너 질문 목록 (최대 12개, 각 1~200자)
        """
        cleaned = [s.strip() for s in openers]
        if any(not s for s in cleaned):
            return "빈 질문은 허용되지 않습니다."
        if len(cleaned) > 12:
            return "오프너는 최대 12개까지 설정할 수 있습니다."
        if any(len(s) > 200 for s in cleaned):
            return "각 질문은 200자를 초과할 수 없습니다."
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            agent.opener_questions = cleaned
            await session.commit()
            return f"오프너 {len(cleaned)}개 설정 완료."

    # ------ 12-2. update_agent_metadata ------

    async def update_agent_metadata(
        name: str | None = None,
        description: str | None = None,
    ) -> str:
        """에이전트의 이름과 설명을 변경합니다.

        둘 중 하나만 지정해도 됩니다. 빈 description은 설명을 비웁니다.

        Args:
            name: 새 에이전트 이름 (생략하면 변경 안 함)
            description: 새 에이전트 설명 (빈 문자열이면 설명 제거)
        """
        if name is None and description is None:
            return "name 또는 description 중 하나는 지정해야 합니다."
        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            changes: list[str] = []
            if name is not None:
                stripped = name.strip()
                if not stripped:
                    return "에이전트 이름은 비워둘 수 없습니다."
                agent.name = stripped
                changes.append(f"이름='{stripped}'")
            if description is not None:
                desc = description.strip()
                agent.description = desc or None
                changes.append("설명=(비움)" if not desc else f"설명='{desc[:30]}…'")
            await session.commit()
            return f"에이전트 메타데이터 변경 완료: {', '.join(changes)}"

    # ------ 13. update_agent_identity_mode ------

    async def update_agent_identity_mode(identity_mode: str) -> str:
        """credential 사용 방식을 변경합니다.

        Args:
            identity_mode: "per_user" 또는 "fixed"
        """
        normalized = identity_mode.strip().lower()
        try:
            validate_identity_mode(normalized)
        except Exception:
            return "credential 사용 방식은 'per_user' 또는 'fixed'만 가능합니다."

        async with async_session_factory() as session:
            agent = await _get_agent_with_session(session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            if normalized == agent.identity_mode:
                return f"credential 사용 방식이 이미 '{normalized}'입니다."

            if normalized == AGENT_IDENTITY_PER_USER:
                active_count = await session.scalar(
                    select(func.count())
                    .select_from(AgentTrigger)
                    .where(
                        AgentTrigger.agent_id == agent_id,
                        AgentTrigger.user_id == user_id,
                        AgentTrigger.status == "active",
                    )
                )
                if active_count:
                    return (
                        "활성 스케줄이 있는 에이전트는 사용자별 credential 모드로 "
                        "변경할 수 없습니다. 먼저 스케줄을 비활성화하세요."
                    )

            agent.identity_mode = normalized
            await session.commit()
            return f"credential 사용 모드 변경 완료: {normalized}"

    # ------ 14. update_recursion_limit ------

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
        name: str | None = None,
        cron_expression: str | None = None,
        interval_minutes: int | None = None,
        scheduled_at: str | None = None,
        timezone: str | None = None,
        conversation_policy: str | None = None,
        target_conversation_id: str | None = None,
        max_runs: int | None = None,
        end_at: str | None = None,
        auto_pause_after_failures: int | None = None,
    ) -> str:
        """크론 스케줄을 생성합니다.

        Args:
            schedule_type: "recurring", "cron", "interval" 또는 "one_time"
            message: 실행 시 전달할 메시지
            name: 스케줄 이름
            cron_expression: 반복 스케줄의 cron 표현식 (recurring/cron일 때 필수)
            interval_minutes: 간격 분 수 (interval일 때 필수)
            scheduled_at: 1회 실행 시점 ISO 8601 (one_time일 때 필수)
            timezone: IANA timezone (기본 Asia/Seoul)
            conversation_policy: 결과 저장 정책 (기본 schedule_thread)
            target_conversation_id: selected_conversation 정책에서 사용할 대화 ID
            max_runs: 최대 성공 실행 횟수
            end_at: 종료 시각 ISO 8601
            auto_pause_after_failures: 연속 실패 자동 일시정지 임계치
        """
        normalized_type = schedule_type.strip().lower()
        trigger_type = "cron" if normalized_type in {"recurring", "cron"} else normalized_type
        schedule_config: dict[str, Any] = {}
        if trigger_type == "cron":
            if not cron_expression:
                return "반복 스케줄에는 cron_expression이 필요합니다."
            parts = cron_expression.strip().split()
            if len(parts) != 5:
                return (
                    f"유효하지 않은 cron 표현식입니다: '{cron_expression}'. "
                    "5개 필드 (분 시 일 월 요일)가 필요합니다."
                )
            schedule_config = {"cron_expression": cron_expression}
        elif trigger_type == "interval":
            if interval_minutes is None:
                return "간격 스케줄에는 interval_minutes가 필요합니다."
            schedule_config = {"interval_minutes": interval_minutes}
        elif trigger_type == "one_time":
            if not scheduled_at:
                return "1회 스케줄에는 scheduled_at이 필요합니다."
            schedule_config = {"scheduled_at": scheduled_at}
        else:
            return (
                "schedule_type은 'recurring' 또는 'one_time'이어야 합니다. "
                "추가로 'cron', 'interval'도 사용할 수 있습니다."
            )

        async with async_session_factory() as session:
            try:
                trigger = await trigger_service.create_trigger(
                    session,
                    agent_id,
                    user_id,
                    TriggerCreate.model_validate(
                        {
                            "name": name,
                            "trigger_type": trigger_type,
                            "schedule_config": schedule_config,
                            "input_message": message,
                            "timezone": timezone or "Asia/Seoul",
                            "conversation_policy": conversation_policy or "schedule_thread",
                            "target_conversation_id": target_conversation_id,
                            "max_runs": max_runs,
                            "end_at": end_at,
                            "auto_pause_after_failures": auto_pause_after_failures,
                        }
                    ),
                )
            except ValueError as exc:
                return f"스케줄 설정이 올바르지 않습니다: {exc}"
            return (
                f"스케줄 생성 완료 (ID: {trigger.id}, 다음 실행: {trigger.next_run_at or '미정'})"
            )

    # ------ 15. update_cron_schedule ------

    def _format_trigger_candidate(trigger: AgentTrigger) -> str:
        return (
            f"ID: {trigger.id}, 이름: {trigger.name}, 상태: {trigger.status}, "
            f"다음 실행: {trigger.next_run_at or '미정'}"
        )

    async def _resolve_trigger_for_write(
        session: AsyncSession,
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> tuple[AgentTrigger | None, str | None]:
        if schedule_id:
            try:
                sid = uuid.UUID(schedule_id)
            except ValueError:
                return None, "유효하지 않은 스케줄 ID입니다."
            result = await session.execute(
                select(AgentTrigger).where(
                    AgentTrigger.id == sid,
                    AgentTrigger.agent_id == agent_id,
                    AgentTrigger.user_id == user_id,
                )
            )
            trigger = result.scalar_one_or_none()
            if not trigger:
                return None, "스케줄을 찾을 수 없습니다."
            return trigger, None

        if not schedule_name:
            return None, "schedule_id 또는 schedule_name이 필요합니다."

        result = await session.execute(
            select(AgentTrigger)
            .where(
                AgentTrigger.agent_id == agent_id,
                AgentTrigger.user_id == user_id,
                AgentTrigger.name == schedule_name,
            )
            .order_by(AgentTrigger.created_at.desc())
        )
        matches = list(result.scalars().all())
        if not matches:
            return None, "스케줄을 찾을 수 없습니다."
        if len(matches) > 1:
            candidates = "\n".join(_format_trigger_candidate(trigger) for trigger in matches)
            return (
                None,
                (
                    f"'{schedule_name}' 이름의 스케줄이 여러 개 있습니다. "
                    f"ID를 지정해 주세요.\n{candidates}"
                ),
            )
        return matches[0], None

    async def update_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
        cron_expression: str | None = None,
        interval_minutes: int | None = None,
        scheduled_at: str | None = None,
        message: str | None = None,
        name: str | None = None,
        timezone: str | None = None,
        conversation_policy: str | None = None,
        target_conversation_id: str | None = None,
        status: str | None = None,
        max_runs: int | None = None,
        end_at: str | None = None,
        auto_pause_after_failures: int | None = None,
    ) -> str:
        """크론 스케줄을 수정합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름 (동명이인이 있으면 ID 필요)
            cron_expression: 새 cron 표현식
            interval_minutes: 새 interval 분 수
            scheduled_at: 새 1회 실행 시점
            message: 새 실행 메시지
            name: 새 스케줄 이름
            timezone: 새 timezone
            conversation_policy: 새 결과 저장 정책
            target_conversation_id: selected_conversation 정책에서 사용할 대화 ID
            status: 새 상태
            max_runs: 새 최대 성공 실행 횟수
            end_at: 새 종료 시각 ISO 8601
            auto_pause_after_failures: 새 연속 실패 자동 일시정지 임계치
        """
        async with async_session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(session, schedule_id, schedule_name)
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."

            update_payload: dict[str, Any] = {}
            if cron_expression:
                update_payload["trigger_type"] = "cron"
                update_payload["schedule_config"] = {"cron_expression": cron_expression}
            if interval_minutes is not None:
                update_payload["trigger_type"] = "interval"
                update_payload["schedule_config"] = {"interval_minutes": interval_minutes}
            if scheduled_at:
                update_payload["trigger_type"] = "one_time"
                update_payload["schedule_config"] = {"scheduled_at": scheduled_at}
            if message:
                update_payload["input_message"] = message
            if name is not None:
                update_payload["name"] = name
            if timezone is not None:
                update_payload["timezone"] = timezone
            if conversation_policy is not None:
                update_payload["conversation_policy"] = conversation_policy
            if target_conversation_id is not None:
                update_payload["target_conversation_id"] = target_conversation_id
            if status is not None:
                update_payload["status"] = status
            if max_runs is not None:
                update_payload["max_runs"] = max_runs
            if end_at is not None:
                update_payload["end_at"] = end_at
            if auto_pause_after_failures is not None:
                update_payload["auto_pause_after_failures"] = auto_pause_after_failures
            try:
                update = TriggerUpdate.model_validate(update_payload)
                await trigger_service.update_trigger(session, trigger, update)
            except ValueError as exc:
                return f"스케줄 설정이 올바르지 않습니다: {exc}"
            return "스케줄 수정 완료."

    # ------ 16. delete_cron_schedule ------

    async def delete_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> str:
        """크론 스케줄을 삭제합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름
        """
        async with async_session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(session, schedule_id, schedule_name)
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."
            await trigger_service.delete_trigger(session, trigger)
            return "스케줄 삭제 완료."

    # ------ 17. enable_cron_schedule ------

    async def enable_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> str:
        """크론 스케줄을 활성화합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름
        """
        async with async_session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(session, schedule_id, schedule_name)
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."
            try:
                await trigger_service.update_trigger(
                    session,
                    trigger,
                    TriggerUpdate(status="active"),
                )
            except ValueError as exc:
                return f"스케줄 설정이 올바르지 않습니다: {exc}"
            return "스케줄 활성화 완료."

    # ------ 18. disable_cron_schedule ------

    async def disable_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> str:
        """크론 스케줄을 비활성화합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름
        """
        async with async_session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(session, schedule_id, schedule_name)
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."
            await trigger_service.update_trigger(session, trigger, TriggerUpdate(status="paused"))
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
            coroutine=add_mcp_tool_to_agent,
            name="add_mcp_tool_to_agent",
            description="에이전트에 MCP 도구 배치 추가 (도구 이름으로)",
        ),
        StructuredTool.from_function(
            coroutine=remove_mcp_tool_from_agent,
            name="remove_mcp_tool_from_agent",
            description="에이전트에서 MCP 도구 배치 제거",
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
            description="에이전트에 서브에이전트 배치 추가",
        ),
        StructuredTool.from_function(
            coroutine=remove_subagent_from_agent,
            name="remove_subagent_from_agent",
            description="에이전트에서 서브에이전트 배치 제거",
        ),
        StructuredTool.from_function(
            coroutine=add_skill_to_agent,
            name="add_skill_to_agent",
            description="에이전트에 스킬 배치 추가",
        ),
        StructuredTool.from_function(
            coroutine=remove_skill_from_agent,
            name="remove_skill_from_agent",
            description="에이전트에서 스킬 배치 제거",
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
            description="채팅 시작 질문(오프너) 변경",
        ),
        StructuredTool.from_function(
            coroutine=update_agent_metadata,
            name="update_agent_metadata",
            description="에이전트 이름/설명 변경 (둘 중 하나 또는 둘 다)",
        ),
        StructuredTool.from_function(
            coroutine=update_agent_identity_mode,
            name="update_agent_identity_mode",
            description="credential 사용 방식 변경 (per_user 또는 fixed)",
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
