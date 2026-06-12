"""Installed Agent Blueprint API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import marketplace_item_not_found
from app.marketplace.schemas import AgentBlueprintOut, CreateAgentFromBlueprintIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation
from app.routers.agents import _agent_to_response
from app.schemas.agent import AgentResponse
from app.services import agent_blueprint_service, audit_service

router = APIRouter(prefix="/api/agent-blueprints", tags=["agent-blueprints"])


def _project_blueprint(
    blueprint: AgentBlueprint,
    installation: MarketplaceInstallation | None,
    *,
    include_spec: bool = True,
) -> AgentBlueprintOut:
    return AgentBlueprintOut(
        id=blueprint.id,
        name=blueprint.name,
        description=blueprint.description,
        icon_id=blueprint.icon_id,
        tags=blueprint.tags,
        categories=blueprint.categories,
        # ``spec`` is large publisher JSON — the list endpoint omits it.
        spec=blueprint.spec if include_spec else None,
        spec_hash=blueprint.spec_hash,
        source_marketplace_item_id=blueprint.source_marketplace_item_id,
        source_marketplace_version_id=blueprint.source_marketplace_version_id,
        installation_id=installation.id if installation else None,
        install_status=(
            installation.install_status if installation else blueprint.install_status
        ),  # type: ignore[arg-type]
        is_dirty=bool(blueprint.is_dirty or (installation and installation.is_dirty)),
        created_agent_count=blueprint.created_agent_count,
        created_at=blueprint.created_at,
        updated_at=blueprint.updated_at,
    )


@router.get("", response_model=list[AgentBlueprintOut])
async def list_agent_blueprints(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[AgentBlueprintOut]:
    rows = (
        await db.execute(
            select(AgentBlueprint, MarketplaceInstallation)
            .outerjoin(
                MarketplaceInstallation,
                (
                    MarketplaceInstallation.installed_agent_blueprint_id
                    == AgentBlueprint.id
                )
                # Uninstall is a soft delete (row kept with
                # ``install_status='uninstalled'``) — joining those rows
                # would duplicate blueprints after a re-install and show
                # a stale "uninstalled" state.
                & (MarketplaceInstallation.install_status != "uninstalled"),
            )
            .where(AgentBlueprint.user_id == user.id)
            # Soft-uninstalled blueprints (row kept, status synced) are
            # hidden so a re-install doesn't surface the old ghost copy.
            .where(AgentBlueprint.install_status != "uninstalled")
            .order_by(AgentBlueprint.updated_at.desc())
        )
    ).all()
    return [
        _project_blueprint(blueprint, installation, include_spec=False)
        for blueprint, installation in rows
    ]


@router.get("/{blueprint_id}", response_model=AgentBlueprintOut)
async def get_agent_blueprint(
    blueprint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> AgentBlueprintOut:
    row = (
        await db.execute(
            select(AgentBlueprint, MarketplaceInstallation)
            .outerjoin(
                MarketplaceInstallation,
                (
                    MarketplaceInstallation.installed_agent_blueprint_id
                    == AgentBlueprint.id
                )
                & (MarketplaceInstallation.install_status != "uninstalled"),
            )
            .where(AgentBlueprint.id == blueprint_id)
            .where(AgentBlueprint.user_id == user.id)
            # A soft-uninstalled blueprint is treated as gone — collapse to
            # 404 per the enumeration-safety convention.
            .where(AgentBlueprint.install_status != "uninstalled")
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        raise marketplace_item_not_found()
    blueprint, installation = row
    return _project_blueprint(blueprint, installation)


@router.post("/{blueprint_id}/create-agent", response_model=AgentResponse, status_code=201)
async def create_agent_from_blueprint(
    blueprint_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> AgentResponse:
    agent = await agent_blueprint_service.create_agent_from_blueprint(
        db,
        blueprint_id=blueprint_id,
        user_id=user.id,
        body=body,
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="agent_blueprint.create_agent",
        target_type="agent",
        target_id=agent.id,
        target_name_snapshot=agent.name,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={"blueprint_id": str(blueprint_id)},
    )
    await db.commit()
    return _agent_to_response(agent)
