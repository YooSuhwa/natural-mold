from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal, cast

import anyio
import httpx
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import agent_not_found, image_not_found
from app.exceptions import ExternalServiceError, ValidationError
from app.models.agent import Agent
from app.schemas.agent import (
    AgentBrief,
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    GenerateImageResponse,
    McpToolBrief,
    ToolBrief,
)
from app.schemas.skill import SkillBrief
from app.services import agent_service, image_service
from app.services.image_preview import get_or_create_image_preview

router = APIRouter(prefix="/api/agents", tags=["agents"])
middleware_router = APIRouter(tags=["middlewares"])


def _sub_agent_image_url(sub: Agent) -> str | None:
    """Compute image_url for a sub-agent (mirrors _agent_to_response logic)."""
    if not sub.image_path:
        return None
    return f"/api/agents/{sub.id}/image?t={int(sub.updated_at.timestamp())}"


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
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        # ``agent.model`` may be None when the FK target was deleted (legacy
        # rows from before the m18 wipe). The schema accepts None and the
        # frontend prompts re-binding instead of crashing the agents list.
        model=agent.model if agent.model is not None else None,
        tools=[ToolBrief(id=link.tool.id, name=link.tool.name) for link in agent.tool_links],
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
        image_url=(
            f"/api/agents/{agent.id}/image?t={int(agent.updated_at.timestamp())}"
            if agent.image_path
            else None
        ),
        template_id=agent.template_id,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        # Set by ``list_agents`` (max conversations.updated_at). For single-row
        # endpoints the attribute is missing → schema defaults to None and the
        # sidebar falls back to ``updated_at`` for sort.
        last_used_at=getattr(agent, "_last_used_at", None),
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agents = await agent_service.list_agents(db, user.id)
    return [_agent_to_response(a) for a in agents]


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.create_agent(db, data, user.id)
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
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    updated = await agent_service.update_agent(db, agent, data)
    return _agent_to_response(updated)


@router.patch("/{agent_id}/favorite", response_model=AgentResponse)
async def toggle_favorite(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    updated = await agent_service.toggle_favorite(db, agent)
    return _agent_to_response(updated)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    await agent_service.delete_agent(db, agent)


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
        # Agent has no image — silent 204 keeps the browser console clean
        # (AgentAvatar falls back to the BotIcon).
        return Response(status_code=204)
    apath = anyio.Path(agent.image_path)
    if not await apath.is_file():
        # File got deleted out from under us. Clean up the orphan path so the
        # next list_agents call returns ``image_url: null`` and the frontend
        # stops requesting this URL altogether.
        agent.image_path = None
        await db.commit()
        return Response(status_code=204)
    target = Path(agent.image_path).resolve()
    if variant == "preview":
        preview = get_or_create_image_preview(
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
