"""Conversational builder Agent finalization ingress (split from ``builder_service``)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_runtime.identity import AGENT_IDENTITY_PER_USER, validate_identity_mode
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import require_execution_prefix
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.error_codes import session_not_found
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from app.schemas.builder import BuilderStatus
from app.services.builder_resource_resolution import _resolve_tools
from app.services.builder_session_service import get_session
from app.services.model_service import resolve_model

logger = logging.getLogger("app.services.builder_service")


# ---------------------------------------------------------------------------
# 빌드 확인 (confirm) — confirm_creation 로직 재사용
# ---------------------------------------------------------------------------


async def confirm_build(
    db: AsyncSession, session: BuilderSession, context: OrgContext
) -> Agent | None:
    """빌드 확인: draft_config를 기반으로 실제 Agent를 생성한다.

    agent_creation_service.confirm_creation()의 도구/모델 매칭 로직을 재사용.
    예외 발생 시 세션을 PREVIEW로 롤백하여 CONFIRMING 고착을 방지한다.
    """
    scoped = await get_session(db, session.id, context)
    if scoped is None:
        raise session_not_found()
    session = scoped
    await resolve_private_actor(
        db, PrivateResourceActor(session.user_id, session.org_id, session.tenant_id)
    )
    config = session.draft_config
    if not config:
        return None

    try:
        # 모델 매칭 — strict 조회 실패 시 fallback
        model_name = config.get("model_name", "")
        model = await resolve_model(db, model_name, strict=True) if model_name else None
        if not model:
            # strict 실패 → default 모델로 fallback
            model = await resolve_model(db, "", strict=False)
        if not model:
            # default도 없으면 → DB의 아무 모델 사용
            from app.models.model import Model as ModelORM

            any_result = await db.execute(select(ModelORM).limit(1))
            model = any_result.scalar_one_or_none()
        if not model:
            raise ValueError(
                "사용 가능한 모델이 없습니다. 모델 설정 페이지에서 모델을 등록해주세요."
            )

        # 항목 매칭 — 이름으로 Tool / McpTool / Skill 3-way 분리 조회
        tools_to_link, mcp_tools_to_link, skills_to_link = await _resolve_tools(
            db, context, config.get("tools", [])
        )

        # Parent creation gate before the Agent insert (archived org denies).
        await require_execution_prefix(db, context)
        # 에이전트 생성
        agent = Agent(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            name=config.get("name_ko") or config.get("name", "새 에이전트"),
            description=config.get("description", ""),
            system_prompt=config.get("system_prompt", ""),
            model_id=model.id,
            identity_mode=validate_identity_mode(
                config.get("identity_mode") or AGENT_IDENTITY_PER_USER
            ),
            middleware_configs=[
                {"type": mw_name, "params": {}} for mw_name in config.get("middlewares", [])
            ],
        )
        agent.tool_links = [AgentToolLink(tool_id=t.id) for t in tools_to_link]
        agent.mcp_tool_links = [AgentMcpToolLink(mcp_tool_id=mt.id) for mt in mcp_tools_to_link]
        agent.skill_links = [AgentSkillLink(skill_id=s.id) for s in skills_to_link]
        db.add(agent)
        await db.flush()  # agent.id 할당을 위해 flush 필요

        session.status = BuilderStatus.COMPLETED
        session.agent_id = agent.id

        await db.commit()
        await db.refresh(agent, ["model", "tool_links"])

        # 이미지 처리: phase7_save가 draft_config["image_url"]을 항상 명시적으로 set.
        #   truthy → 임시 파일을 Agent 디렉토리로 이동
        #   None/falsy → 사용자가 phase6에서 명시적 skip
        image_url = (config or {}).get("image_url")
        if image_url:
            await _transfer_builder_image(session, agent, image_url)
            # agent.image_path 변경을 DB에 반영
            await db.commit()
            await db.refresh(agent)

        # 이미지 생성이 commit하면 관계가 expire되므로 selectinload로 재로드
        result = await db.execute(
            select(Agent)
            .where(Agent.id == agent.id)
            .options(
                selectinload(Agent.model),
                selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
                selectinload(Agent.mcp_tool_links).selectinload(AgentMcpToolLink.mcp_tool),
                selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
            )
        )
        return result.scalar_one()
    except Exception:
        # CONFIRMING 고착 방지: 예외 발생 시 PREVIEW로 롤백
        await db.rollback()
        session.status = BuilderStatus.PREVIEW
        await db.commit()
        raise


def _transfer_builder_image_sync(
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    public_url: str,
) -> str | None:
    """Sync I/O — copy file + cleanup builder temp dir. asyncio.to_thread로 호출.

    Returns: agent.image_path 값 (None이면 실패).
    """
    import shutil
    from pathlib import Path

    from app.agent_runtime.builder_v3.image_gen import resolve_local_path
    from app.services.agent_image_paths import agent_image_dir

    filename = Path(public_url).name
    src = resolve_local_path(str(session_id), filename)
    if not src:
        logger.warning("Builder image source not found: %s", public_url)
        return None

    dest_dir = agent_image_dir() / str(agent_id)
    dest = dest_dir / f"avatar{src.suffix}"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 직접 copy — 파일 사라지면 FileNotFoundError로 처리 (TOCTOU 방어)
        shutil.copy(src, dest)
    except FileNotFoundError:
        logger.warning("Builder image disappeared before copy: %s", src)
        return None
    except Exception:
        logger.warning("Builder image transfer failed", exc_info=True)
        return None

    # Cleanup: builder temp dir 정리 (실패해도 무시)
    builder_dir = src.parent
    if builder_dir.name == str(session_id):
        shutil.rmtree(builder_dir, ignore_errors=True)

    return str(dest)


async def _transfer_builder_image(
    session: BuilderSession,
    agent: Agent,
    public_url: str,
) -> None:
    """Phase 6 임시 이미지 → Agent 디렉토리 복사 (async wrapper).

    실패해도 Agent 생성은 유지한다.
    """
    result = await asyncio.to_thread(_transfer_builder_image_sync, session.id, agent.id, public_url)
    if result:
        agent.image_path = result
