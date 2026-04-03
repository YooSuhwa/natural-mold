from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.agent_runtime.fix_agent import run_fix_conversation
from app.models.agent import Agent
from app.models.model import Model
from app.models.tool import Tool
from app.schemas.agent import AgentUpdate
from app.schemas.fix_agent import FixAgentChanges, FixAgentMessageRequest, FixAgentResponse
from app.services import agent_service

router = APIRouter(prefix="/api/agents", tags=["fix-agent"])


@router.post("/{agent_id}/fix", response_model=FixAgentResponse)
async def fix_agent(
    agent_id: uuid.UUID,
    data: FixAgentMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    result = await db.execute(
        select(Tool.name).where(or_(Tool.user_id == user.id, Tool.is_system.is_(True)))
    )
    available_tools = [r[0] for r in result.all()]

    result = await db.execute(select(Model.display_name))
    available_models = [r[0] for r in result.all()]

    params = agent.model_params or {}
    agent_info = {
        "name": agent.name,
        "description": agent.description or "",
        "system_prompt": agent.system_prompt,
        "model_name": agent.model.display_name,
        "tool_names": [link.tool.name for link in agent.tool_links],
        "temperature": params.get("temperature", 0.7),
        "top_p": params.get("top_p", 1.0),
        "max_tokens": params.get("max_tokens", 4096),
    }

    response = await run_fix_conversation(
        agent_info=agent_info,
        conversation_history=data.conversation_history,
        user_message=data.content,
        available_tools=available_tools,
        available_models=available_models,
    )

    updated_history = [*data.conversation_history]
    updated_history.append({"role": "user", "content": data.content})
    updated_history.append({"role": "assistant", "content": response["raw_content"]})

    changes_model: FixAgentChanges | None = None
    if response.get("changes"):
        changes_model = FixAgentChanges(**response["changes"])

    if response["action"] == "apply" and changes_model:
        await _apply_changes(db, agent, changes_model, user.id)

    return FixAgentResponse(
        content=response["content"],
        action=response["action"],
        changes=changes_model,
        summary=response.get("summary"),
        question=response.get("question"),
        conversation_history=updated_history,
    )


async def _apply_changes(
    db: AsyncSession,
    agent: Agent,
    changes: FixAgentChanges,
    user_id: uuid.UUID,
) -> None:
    update_data: dict = {}

    if changes.system_prompt is not None:
        update_data["system_prompt"] = changes.system_prompt
    if changes.name is not None:
        update_data["name"] = changes.name
    if changes.description is not None:
        update_data["description"] = changes.description
    if changes.model_params is not None:
        update_data["model_params"] = changes.model_params

    if changes.model_name:
        result = await db.execute(
            select(Model).where(
                func.lower(Model.display_name) == changes.model_name.lower()
            )
        )
        model = result.scalar_one_or_none()
        if model:
            update_data["model_id"] = model.id

    current_tool_ids = [link.tool_id for link in agent.tool_links]

    # Batch resolve tool names → IDs in a single query
    if changes.add_tools:
        result = await db.execute(
            select(Tool.id).where(
                or_(Tool.user_id == user_id, Tool.is_system.is_(True)),
                func.lower(Tool.name).in_([n.lower() for n in changes.add_tools]),
            )
        )
        for row in result.all():
            if row[0] not in current_tool_ids:
                current_tool_ids.append(row[0])

    if changes.remove_tools:
        result = await db.execute(
            select(Tool.id).where(
                func.lower(Tool.name).in_([n.lower() for n in changes.remove_tools]),
            )
        )
        ids_to_remove = {row[0] for row in result.all()}
        current_tool_ids = [tid for tid in current_tool_ids if tid not in ids_to_remove]

    if changes.add_tools or changes.remove_tools:
        update_data["tool_ids"] = current_tool_ids

    if update_data:
        await agent_service.update_agent(db, agent, AgentUpdate(**update_data))
