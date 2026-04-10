from __future__ import annotations

import uuid
from typing import Any

import anyio
import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import agent_not_found, image_file_not_found, image_not_found
from app.exceptions import ExternalServiceError, ValidationError
from app.models.agent import Agent
from app.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    GenerateImageResponse,
    ToolBrief,
)
from app.schemas.skill import SkillBrief
from app.services import agent_service, image_service

router = APIRouter(prefix="/api/agents", tags=["agents"])
middleware_router = APIRouter(tags=["middlewares"])


def _agent_to_response(agent: Agent) -> AgentResponse:
    """Convert Agent model to AgentResponse with tool configs."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        model=agent.model,
        tools=[
            ToolBrief(id=link.tool.id, name=link.tool.name, agent_config=link.config)
            for link in agent.tool_links
        ],
        skills=[
            SkillBrief(id=link.skill_id, name=link.skill.name, description=link.skill.description)
            for link in agent.skill_links
        ],
        middleware_configs=agent.middleware_configs or [],
        status=agent.status,
        is_favorite=agent.is_favorite,
        model_params=agent.model_params,
        image_url=(
            f"/api/agents/{agent.id}/image?t={int(agent.updated_at.timestamp())}"
            if agent.image_path
            else None
        ),
        template_id=agent.template_id,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
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
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent or not agent.image_path:
        raise image_not_found()
    apath = anyio.Path(agent.image_path)
    if not await apath.is_file():
        raise image_file_not_found()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    media = media_map.get(apath.suffix, "image/png")
    return FileResponse(str(apath), media_type=media)


@middleware_router.get("/api/middlewares")
async def list_middlewares() -> list[dict[str, Any]]:
    """Return the available middleware catalog.

    deepagents가 자동 추가하는 빌트인 미들웨어는 제외한다.
    """
    return get_middleware_registry(exclude_builtin=True)
