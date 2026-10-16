from __future__ import annotations

import uuid
from contextlib import suppress

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.access import ensure_key_can_access_deployment
from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.authz.agent_api_scope import private_deployment_predicate
from app.exceptions import ForbiddenError, NotFoundError
from app.models.agent_api import AgentDeployment


async def resolve_deployment_for_agent_id(
    db: AsyncSession,
    principal: ApiKeyPrincipal,
    agent_id: str,
    *,
    required_scope: str,
) -> AgentDeployment:
    principal = await refresh_api_key_principal(db, principal)
    principal.require_scope(required_scope)

    conditions = [AgentDeployment.public_id == agent_id]
    with suppress(ValueError):
        conditions.append(AgentDeployment.agent_id == uuid.UUID(agent_id))

    result = await db.execute(
        select(AgentDeployment)
        .where(private_deployment_predicate(principal.context))
        .where(AgentDeployment.status == "active")
        .where(or_(*conditions))
        .options(selectinload(AgentDeployment.agent))
        .execution_options(populate_existing=True, autoflush=False)
    )
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")

    ensure_key_can_access_deployment(principal, deployment)

    if required_scope == "stream" and not deployment.allow_streaming:
        raise ForbiddenError("AGENT_API_STREAM_DISABLED", "streaming is disabled")
    if required_scope == "background" and not deployment.allow_background:
        raise ForbiddenError("AGENT_API_BACKGROUND_DISABLED", "background is disabled")
    return deployment
