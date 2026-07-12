"""Assistant 쓰기 도구 — 에이전트 설정 그룹 (프롬프트/모델/메타데이터 등)."""

from __future__ import annotations

import copy

from langchain_core.tools import StructuredTool
from sqlalchemy import func, select

from app.agent_runtime.assistant.tools.write_tools.context import (
    WriteToolContext,
    get_agent_with_session,
)
from app.agent_runtime.identity import AGENT_IDENTITY_PER_USER, validate_identity_mode
from app.models.agent_trigger import AgentTrigger
from app.services.model_service import resolve_model


def build_agent_config_tools(ctx: WriteToolContext) -> list[StructuredTool]:
    """에이전트 설정 도구 8개를 생성한다."""

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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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

        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
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

        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            if normalized == agent.identity_mode:
                return f"credential 사용 방식이 이미 '{normalized}'입니다."

            if normalized == AGENT_IDENTITY_PER_USER:
                active_count = await session.scalar(
                    select(func.count())
                    .select_from(AgentTrigger)
                    .where(
                        AgentTrigger.agent_id == ctx.agent_id,
                        AgentTrigger.user_id == ctx.user_id,
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
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."
            params = dict(agent.model_params or {})
            params["recursion_limit"] = limit
            agent.model_params = params
            await session.commit()
            return f"재귀 한도를 {limit}으로 변경했습니다."

    return [
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
    ]
