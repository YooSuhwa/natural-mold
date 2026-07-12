from __future__ import annotations

import uuid
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import agent_not_found, image_not_found
from app.exceptions import ExternalServiceError, ValidationError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.schemas.agent import (
    AgentBrief,
    AgentCreate,
    AgentResponse,
    AgentSummaryResponse,
    AgentUpdate,
    GenerateImageResponse,
    McpToolBrief,
    ToolBrief,
)
from app.schemas.skill import SkillBrief
from app.services import agent_service, audit_service, image_service
from app.services.agent_image_paths import build_agent_image_url, resolve_agent_image_path
from app.services.image_preview import get_or_create_image_preview_async
from app.tools.registry import registry as tool_registry

router = APIRouter(prefix="/api/agents", tags=["agents"])
middleware_router = APIRouter(tags=["middlewares"])


def _sub_agent_image_url(sub: Agent) -> str | None:
    """Compute image_url for a sub-agent (mirrors _agent_to_response logic)."""
    return build_agent_image_url(sub.id, updated_at=sub.updated_at, image_path=sub.image_path)


def _require_standard_profile(agent: Agent) -> None:
    """히든 런타임 에이전트(skill builder 등)는 변조 불가 — enumeration-safe 404.

    404로 통일해 존재 여부 oracle을 만들지 않는다 (없음/숨김 응답 동일).
    GET 단건은 빌더 챗 서피스가 에이전트 메타를 읽어야 하므로 막지 않는다.
    """
    if agent.runtime_profile != AGENT_RUNTIME_PROFILE_STANDARD:
        raise agent_not_found()


def _tool_icon_id(definition_key: str) -> str | None:
    """도구 registry 정의에서 icon_id를 해석. Tool ORM에는 icon_id 컬럼이 없고
    definition_key → 메모리 registry 정의가 ground truth다."""
    definition = tool_registry.get(definition_key)
    return definition.icon_id if definition is not None else None


def _agent_to_response(agent: Agent) -> AgentResponse:
    """Convert Agent model to AgentResponse with tool configs."""
    fallback_ids: list[uuid.UUID] = []
    if agent.model_fallback_list:
        for raw in agent.model_fallback_list:
            try:
                fallback_ids.append(uuid.UUID(str(raw)))
            except (TypeError, ValueError):
                # Skip malformed legacy entries; surface only valid UUIDs.
                continue
    return AgentResponse(
        id=agent.id,
        runtime_name=agent.runtime_name,
        identity_mode=agent.identity_mode,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        # ``agent.model`` may be None when the FK target was deleted (legacy
        # rows from before the m18 wipe). The schema accepts None and the
        # frontend prompts re-binding instead of crashing the agents list.
        model=agent.model if agent.model is not None else None,
        tools=[
            ToolBrief(
                id=link.tool.id,
                name=link.tool.name,
                icon_id=_tool_icon_id(link.tool.definition_key),
            )
            for link in agent.tool_links
        ],
        mcp_tools=[
            McpToolBrief(
                id=link.mcp_tool.id,
                name=link.mcp_tool.name,
                server_id=link.mcp_tool.server_id,
            )
            for link in agent.mcp_tool_links
        ],
        skills=[
            SkillBrief(
                id=link.skill_id,
                name=link.skill.name,
                slug=link.skill.slug,
                kind=cast(Literal["text", "package"], link.skill.kind),
                description=link.skill.description,
            )
            for link in agent.skill_links
        ],
        sub_agents=[
            AgentBrief(
                id=link.sub_agent.id,
                name=link.sub_agent.name,
                description=link.sub_agent.description,
                image_url=_sub_agent_image_url(link.sub_agent),
            )
            for link in agent.sub_agent_links
        ],
        middleware_configs=agent.middleware_configs or [],
        status=agent.status,
        is_favorite=agent.is_favorite,
        model_params=agent.model_params,
        opener_questions=agent.opener_questions,
        model_fallback_ids=fallback_ids,
        image_url=build_agent_image_url(
            agent.id,
            updated_at=agent.updated_at,
            image_path=agent.image_path,
        ),
        template_id=agent.template_id,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        # Set by ``list_agents`` (max conversations.updated_at). For single-row
        # endpoints the attribute is missing → schema defaults to None and the
        # sidebar falls back to ``updated_at`` for sort.
        last_used_at=getattr(agent, "_last_used_at", None),
        unread_count=getattr(agent, "_unread_count", 0),
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agents = await agent_service.list_agents(db, user.id)
    return [_agent_to_response(a) for a in agents]


@router.get("/summary", response_model=list[AgentSummaryResponse])
async def list_agent_summaries(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await agent_service.list_agent_summaries(db, user.id)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    data: AgentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.create_agent(db, data, user.id)
    await audit_service.record_self_event(
        db,
        user,
        action="agent.create",
        target_type="agent",
        target_id=agent.id,
        target_name=agent.name,
        request=request,
        metadata={
            "model_id": str(data.model_id),
            "tool_count": len(data.tool_ids),
            "mcp_tool_count": len(data.mcp_tool_ids),
            "skill_count": len(data.skill_ids),
            "sub_agent_count": len(data.sub_agent_ids),
            "system_prompt_changed": True,
        },
    )
    await db.commit()
    return _agent_to_response(agent)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    return _agent_to_response(agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    _require_standard_profile(agent)
    updated = await agent_service.update_agent(db, agent, data)
    changed_fields = sorted(data.model_fields_set - {"system_prompt"})
    await audit_service.record_self_event(
        db,
        user,
        action="agent.update",
        target_type="agent",
        target_id=updated.id,
        target_name=updated.name,
        request=request,
        metadata={
            "changed_fields": changed_fields,
            "system_prompt_changed": "system_prompt" in data.model_fields_set,
        },
    )
    await db.commit()
    return _agent_to_response(updated)


@router.patch("/{agent_id}/favorite", response_model=AgentResponse)
async def toggle_favorite(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    _require_standard_profile(agent)
    updated = await agent_service.toggle_favorite(db, agent)
    await audit_service.record_self_event(
        db,
        user,
        action="agent.favorite",
        target_type="agent",
        target_id=updated.id,
        target_name=updated.name,
        request=request,
        metadata={"is_favorite": updated.is_favorite},
    )
    await db.commit()
    return _agent_to_response(updated)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    _require_standard_profile(agent)
    await audit_service.record_self_event(
        db,
        user,
        action="agent.delete",
        target_type="agent",
        target_id=agent.id,
        target_name=agent.name,
        request=request,
    )
    await agent_service.delete_agent(db, agent)
    await db.commit()


@router.post("/{agent_id}/image", response_model=GenerateImageResponse)
async def generate_agent_image(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    _require_standard_profile(agent)
    try:
        image_url = await image_service.generate_agent_image(db, agent)
    except ValueError as e:
        raise ValidationError("IMAGE_GEN_CONFIG", str(e)) from e
    except (httpx.HTTPStatusError, RuntimeError) as e:
        raise ExternalServiceError("IMAGE_GEN_FAILED", str(e)) from e
    return GenerateImageResponse(image_url=image_url)


@router.get("/{agent_id}/image")
async def get_agent_image(
    agent_id: uuid.UUID,
    variant: Literal["original", "preview"] = Query("original"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise image_not_found()
    if not agent.image_path:
        target = resolve_agent_image_path(None, agent_id=agent.id)
    else:
        target = resolve_agent_image_path(agent.image_path, agent_id=agent.id)
    if target is None:
        # Agent has no readable image — silent 204 keeps the browser console
        # clean (AgentAvatar falls back to the BotIcon). Do not mutate the DB
        # from this read path: a transient worktree/CWD mismatch must not erase
        # an otherwise valid avatar reference.
        return Response(status_code=204)
    if variant == "preview":
        preview = await get_or_create_image_preview_async(
            target,
            cache_dir=target.parent / ".previews",
            cache_name=f"agent-{agent.id}",
        )
        if preview is not None:
            return FileResponse(
                preview,
                media_type="image/webp",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    media = media_map.get(target.suffix, "image/png")
    return FileResponse(str(target), media_type=media)


@middleware_router.get("/api/middlewares")
async def list_middlewares() -> list[dict[str, Any]]:
    """Return the available middleware catalog.

    deepagents가 자동 추가하는 빌트인 미들웨어는 제외한다.
    """
    return get_middleware_registry(exclude_builtin=True)
