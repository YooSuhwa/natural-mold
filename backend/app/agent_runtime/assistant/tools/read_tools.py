"""Assistant 읽기 도구 — 16개 Safe 도구 (DB 수정 없음).

도구 목록:
1. get_agent_config
2. get_model_config
3. get_tool_config
4. list_available_tools
5. list_available_middlewares
6. list_available_subagents
7. list_available_models
8. get_agent_required_secrets
9. get_user_secrets
10. get_chat_openers
11. get_recursion_limit
12. list_permanent_files
13. get_file_content
14. search_system_prompt
15. list_cron_schedules
16. get_cron_schedule
"""

from __future__ import annotations

import json
import uuid

from langchain_core.tools import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY
from app.database import async_session as async_session_factory
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.model import Model
from app.services.tool_service import get_tools_catalog


def build_read_tools(
    db: AsyncSession,  # noqa: ARG001 — kept for interface compatibility
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[StructuredTool]:
    """Assistant 읽기 도구 16개를 생성한다.

    각 도구는 호출 시마다 fresh DB 세션을 생성하여 사용한다.
    LangGraph 에이전트의 도구 실행은 빌드 시점의 클로저 DB 세션이
    이미 닫혀 있을 수 있으므로, 매 호출마다 새 세션을 열어야 안전하다.
    """

    # ------ helpers ------

    async def _get_agent() -> Agent | None:
        async with async_session_factory() as session:
            return await get_agent_with_eager_load(session, agent_id, user_id)

    # ------ 1. get_agent_config ------

    async def get_agent_config() -> str:
        """현재 에이전트의 전체 설정을 조회합니다."""
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        tools_info = [
            {
                "name": link.tool.name,
                "description": link.tool.description,
                "type": link.tool.type,
                "config": link.config or {},
            }
            for link in agent.tool_links
        ]
        mw_info = []
        for mc in agent.middleware_configs or []:
            mtype = mc.get("type", "")
            reg = MIDDLEWARE_REGISTRY.get(mtype, {})
            mw_info.append(
                {
                    "type": mtype,
                    "display_name": reg.get("display_name", mtype),
                    "params": mc.get("params", {}),
                }
            )
        return json.dumps(
            {
                "agent_id": str(agent.id),
                "name": agent.name,
                "description": agent.description,
                "system_prompt": agent.system_prompt,
                "model_name": (
                    f"{agent.model.provider}:{agent.model.model_name}" if agent.model else "unknown"
                ),
                "model_params": agent.model_params,
                "tools": tools_info,
                "middlewares": mw_info,
                "skills": [
                    {"name": link.skill.name, "description": link.skill.description}
                    for link in agent.skill_links
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    # ------ 2. get_model_config ------

    async def get_model_config() -> str:
        """현재 에이전트의 모델 설정을 조회합니다."""
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        return json.dumps(
            {
                "model_name": (
                    f"{agent.model.provider}:{agent.model.model_name}" if agent.model else "unknown"
                ),
                "display_name": agent.model.display_name if agent.model else "",
                "model_params": agent.model_params or {},
            },
            ensure_ascii=False,
        )

    # ------ 3. get_tool_config ------

    async def get_tool_config(tool_name: str) -> str:
        """특정 도구의 설정을 조회합니다.

        Args:
            tool_name: 조회할 도구 이름
        """
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        for link in agent.tool_links:
            if link.tool.name.lower() == tool_name.lower():
                return json.dumps(
                    {
                        "name": link.tool.name,
                        "type": link.tool.type,
                        "description": link.tool.description,
                        "config": link.config or {},
                    },
                    ensure_ascii=False,
                )
        return f"도구 '{tool_name}'을(를) 찾을 수 없습니다."

    # ------ 4. list_available_tools ------

    async def list_available_tools() -> str:
        """시스템에서 사용 가능한 도구 목록을 조회합니다."""
        async with async_session_factory() as session:
            items = await get_tools_catalog(session, user_id)
            return json.dumps(items, ensure_ascii=False, indent=2)

    # ------ 5. list_available_middlewares ------

    async def list_available_middlewares() -> str:
        """시스템에서 사용 가능한 미들웨어 목록을 조회합니다."""
        items = [
            {
                "type": key,
                "display_name": entry["display_name"],
                "description": entry["description"],
                "category": entry["category"],
            }
            for key, entry in MIDDLEWARE_REGISTRY.items()
        ]
        return json.dumps(items, ensure_ascii=False, indent=2)

    # ------ 6. list_available_subagents ------

    async def list_available_subagents() -> str:
        """서브에이전트로 사용 가능한 에이전트 목록을 조회합니다."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Agent.id, Agent.name, Agent.description, Agent.model_id).where(
                    Agent.user_id == user_id, Agent.id != agent_id
                )
            )
            rows = result.all()
            # model display_name은 별도 쿼리로 가져온다
            model_ids = {r.model_id for r in rows if r.model_id}
            model_names: dict[str, str] = {}
            if model_ids:
                model_result = await session.execute(
                    select(Model.id, Model.display_name).where(Model.id.in_(model_ids))
                )
                model_names = {r.id: r.display_name for r in model_result.all()}
            items = [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "description": r.description,
                    "model": model_names.get(r.model_id, ""),
                }
                for r in rows
            ]
            return json.dumps(items, ensure_ascii=False, indent=2)

    # ------ 7. list_available_models ------

    async def list_available_models() -> str:
        """사용 가능한 모델 목록을 조회합니다."""
        async with async_session_factory() as session:
            result = await session.execute(select(Model))
            models = result.scalars().all()
            items = [
                {
                    "id": str(m.id),
                    "display_name": m.display_name,
                    "provider": m.provider,
                    "model_name": m.model_name,
                }
                for m in models
            ]
            return json.dumps(items, ensure_ascii=False, indent=2)

    # ------ 8. get_agent_required_secrets ------

    async def get_agent_required_secrets() -> str:
        """에이전트에 필요한 API 키 목록을 조회합니다."""
        # PoC: 도구 타입별 필요 키 반환
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        required: set[str] = set()
        for link in agent.tool_links:
            if "naver" in link.tool.name.lower():
                required.update(["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"])
            if "google" in link.tool.name.lower():
                required.update(["GOOGLE_API_KEY", "GOOGLE_CSE_ID"])
        return json.dumps(
            {
                "required": sorted(required),
                "registered": [],  # PoC: 시크릿 스토어 미구현
                "missing": sorted(required),
            },
            ensure_ascii=False,
        )

    # ------ 9. get_user_secrets ------

    async def get_user_secrets() -> str:
        """사용자가 등록한 시크릿 목록을 조회합니다."""
        # PoC: 시크릿 스토어 미구현
        return json.dumps({"secrets": []}, ensure_ascii=False)

    # ------ 10. get_chat_openers ------

    async def get_chat_openers() -> str:
        """현재 에이전트의 채팅 시작 질문 목록을 조회합니다."""
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        # chat_openers는 Agent.model_params에 저장 (또는 별도 필드)
        openers = (agent.model_params or {}).get("chat_openers", [])
        return json.dumps({"chat_openers": openers}, ensure_ascii=False)

    # ------ 11. get_recursion_limit ------

    async def get_recursion_limit() -> str:
        """현재 에이전트의 재귀 한도를 조회합니다."""
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        limit = (agent.model_params or {}).get("recursion_limit", 25)
        return json.dumps({"recursion_limit": limit}, ensure_ascii=False)

    # ------ 12. list_permanent_files ------

    async def list_permanent_files() -> str:
        """에이전트에 업로드된 영구 파일 목록을 조회합니다."""
        # PoC: 파일 업로드 미구현
        return json.dumps({"files": []}, ensure_ascii=False)

    # ------ 13. get_file_content ------

    async def get_file_content(file_id: str) -> str:
        """파일 내용을 미리봅니다.

        Args:
            file_id: 파일 고유 ID
        """
        return f"파일 '{file_id}'을(를) 찾을 수 없습니다."

    # ------ 14. search_system_prompt ------

    async def search_system_prompt(keyword: str) -> str:
        """시스템 프롬프트에서 키워드를 검색합니다.

        Args:
            keyword: 검색할 키워드
        """
        agent = await _get_agent()
        if not agent:
            return "에이전트를 찾을 수 없습니다."
        prompt = agent.system_prompt or ""
        matches = []
        for i, line in enumerate(prompt.split("\n"), 1):
            if keyword.lower() in line.lower():
                matches.append({"text": line.strip(), "line_number": i})
        return json.dumps(
            {
                "found": len(matches) > 0,
                "matches": matches,
            },
            ensure_ascii=False,
        )

    # ------ 15. list_cron_schedules ------

    async def list_cron_schedules() -> str:
        """에이전트의 크론 스케줄 목록을 조회합니다."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(AgentTrigger).where(AgentTrigger.agent_id == agent_id)
            )
            triggers = result.scalars().all()
            items = [
                {
                    "id": str(t.id),
                    "type": t.trigger_type,
                    "schedule": t.schedule_config,
                    "message": t.input_message,
                    "status": t.status,
                    "last_run_at": str(t.last_run_at) if t.last_run_at else None,
                    "run_count": t.run_count,
                }
                for t in triggers
            ]
            return json.dumps(items, ensure_ascii=False, indent=2)

    # ------ 16. get_cron_schedule ------

    async def get_cron_schedule(schedule_id: str) -> str:
        """특정 크론 스케줄의 상세 정보를 조회합니다.

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
            t = result.scalar_one_or_none()
            if not t:
                return "스케줄을 찾을 수 없습니다."
            return json.dumps(
                {
                    "id": str(t.id),
                    "type": t.trigger_type,
                    "schedule": t.schedule_config,
                    "message": t.input_message,
                    "status": t.status,
                    "last_run_at": str(t.last_run_at) if t.last_run_at else None,
                    "next_run_at": str(t.next_run_at) if t.next_run_at else None,
                    "run_count": t.run_count,
                },
                ensure_ascii=False,
            )

    # ------ Build tools list ------

    return [
        StructuredTool.from_function(
            coroutine=get_agent_config,
            name="get_agent_config",
            description="현재 에이전트의 전체 설정 조회 (도구, 미들웨어, 프롬프트, 모델)",
        ),
        StructuredTool.from_function(
            coroutine=get_model_config,
            name="get_model_config",
            description="현재 에이전트의 모델 설정 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_tool_config,
            name="get_tool_config",
            description="특정 도구의 설정을 조회",
        ),
        StructuredTool.from_function(
            coroutine=list_available_tools,
            name="list_available_tools",
            description="시스템에서 사용 가능한 도구 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=list_available_middlewares,
            name="list_available_middlewares",
            description="시스템에서 사용 가능한 미들웨어 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=list_available_subagents,
            name="list_available_subagents",
            description="서브에이전트로 사용 가능한 에이전트 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=list_available_models,
            name="list_available_models",
            description="사용 가능한 LLM 모델 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_agent_required_secrets,
            name="get_agent_required_secrets",
            description="에이전트에 필요한 API 키 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_user_secrets,
            name="get_user_secrets",
            description="사용자가 등록한 시크릿(API 키) 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_chat_openers,
            name="get_chat_openers",
            description="현재 에이전트의 채팅 시작 질문 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_recursion_limit,
            name="get_recursion_limit",
            description="현재 에이전트의 재귀 한도 조회",
        ),
        StructuredTool.from_function(
            coroutine=list_permanent_files,
            name="list_permanent_files",
            description="에이전트에 업로드된 영구 파일(RAG용) 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_file_content,
            name="get_file_content",
            description="파일 내용 미리보기",
        ),
        StructuredTool.from_function(
            coroutine=search_system_prompt,
            name="search_system_prompt",
            description="시스템 프롬프트에서 키워드 검색",
        ),
        StructuredTool.from_function(
            coroutine=list_cron_schedules,
            name="list_cron_schedules",
            description="에이전트의 크론 스케줄 목록 조회",
        ),
        StructuredTool.from_function(
            coroutine=get_cron_schedule,
            name="get_cron_schedule",
            description="특정 크론 스케줄의 상세 정보 조회",
        ),
    ]
