from __future__ import annotations

import uuid
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.dependencies import CurrentUser, get_current_user, get_db
from app.exceptions import NotFoundError
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


def _agent_to_response(agent) -> AgentResponse:
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
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
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
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
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
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
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
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
    await agent_service.delete_agent(db, agent)


@router.post("/{agent_id}/image", response_model=GenerateImageResponse)
async def generate_agent_image(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
    image_url = await image_service.generate_agent_image(db, agent)
    return GenerateImageResponse(image_url=image_url)


@router.get("/{agent_id}/image")
async def get_agent_image(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent or not agent.image_path:
        raise NotFoundError("IMAGE_NOT_FOUND", "이미지를 찾을 수 없습니다")
    apath = anyio.Path(agent.image_path)
    if not await apath.is_file():
        raise NotFoundError("IMAGE_NOT_FOUND", "이미지 파일을 찾을 수 없습니다")
    return FileResponse(Path(agent.image_path), media_type="image/png")


@middleware_router.get("/api/middlewares")
async def list_middlewares():
    """Return the available middleware catalog."""
    return get_middleware_registry()
