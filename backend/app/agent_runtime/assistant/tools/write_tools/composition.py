"""Assistant 쓰기 도구 — 구성 그룹 (미들웨어/서브에이전트/스킬 add/remove)."""

from __future__ import annotations

import uuid

from langchain_core.tools import StructuredTool
from sqlalchemy import func, select

from app.agent_runtime.assistant.tools.write_tools.context import (
    WriteToolContext,
    get_agent_with_session,
)
from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.skill import AgentSkillLink, Skill


def build_composition_tools(ctx: WriteToolContext) -> list[StructuredTool]:
    """미들웨어/서브에이전트/스킬 구성 도구 6개를 생성한다."""

    # ------ 3. add_middleware_to_agent ------

    async def add_middleware_to_agent(middleware_names: list[str]) -> str:
        """에이전트에 미들웨어를 추가합니다 (배치 지원).

        Args:
            middleware_names: 추가할 미들웨어 type 키 목록
        """
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
                        Agent.user_id == ctx.user_id,
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing = {link.skill.name.lower() for link in agent.skill_links}
            lower_names = [n.lower() for n in skill_names if n.lower() not in existing]
            if not lower_names:
                return "모든 스킬이 이미 추가되어 있습니다."

            result = await session.execute(
                select(Skill)
                .where(
                    Skill.user_id == ctx.user_id,
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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

    return [
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
    ]
