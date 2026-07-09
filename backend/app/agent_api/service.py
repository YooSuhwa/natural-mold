from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.security import generate_api_key
from app.agent_runtime.identity import AGENT_IDENTITY_FIXED
from app.exceptions import NotFoundError, ValidationError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentDeployment
from app.schemas.agent_api import (
    AgentApiKeyCreate,
    AgentApiKeyDeploymentRef,
    AgentDeploymentCandidateResponse,
    AgentDeploymentCreate,
    AgentDeploymentResponse,
    AgentDeploymentUpdate,
)

FIXED_IDENTITY_REASON_CODE = "fixed_identity_required"


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def expires_at_from_days(days: int | None) -> datetime | None:
    if days is None:
        return None
    return utc_now_naive() + timedelta(days=days)


def deployment_to_response(row: AgentDeployment) -> AgentDeploymentResponse:
    return AgentDeploymentResponse(
        id=row.id,
        agent_id=row.agent_id,
        agent_name=row.agent.name if row.agent is not None else "",
        public_id=row.public_id,
        status=row.status,
        allow_streaming=row.allow_streaming,
        allow_background=row.allow_background,
        rate_limit_per_minute=row.rate_limit_per_minute,
        daily_token_limit=row.daily_token_limit,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def serialize_key_deployments(key: AgentApiKey) -> list[AgentApiKeyDeploymentRef]:
    refs: list[AgentApiKeyDeploymentRef] = []
    for link in key.deployment_links:
        deployment = link.deployment
        if deployment is None:
            continue
        refs.append(
            AgentApiKeyDeploymentRef(
                deployment_id=deployment.id,
                agent_id=deployment.agent_id,
                agent_name=deployment.agent.name if deployment.agent is not None else "",
                public_id=deployment.public_id,
                status=deployment.status,
            )
        )
    return refs


def _key_options():
    return selectinload(AgentApiKey.deployment_links).selectinload(
        AgentApiKeyDeployment.deployment
    ).selectinload(AgentDeployment.agent)


async def list_deployments(db: AsyncSession, user_id: uuid.UUID) -> list[AgentDeployment]:
    result = await db.execute(
        select(AgentDeployment)
        .where(AgentDeployment.user_id == user_id)
        .options(selectinload(AgentDeployment.agent))
        .order_by(AgentDeployment.created_at.desc())
    )
    return list(result.scalars().all())


async def list_deployment_candidates(
    db: AsyncSession, user_id: uuid.UUID
) -> list[AgentDeploymentCandidateResponse]:
    agents_result = await db.execute(
        select(Agent)
        .where(
            Agent.user_id == user_id,
            # 히든 런타임 에이전트는 API 배포 후보에서 제외.
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
        .order_by(Agent.name.asc())
    )
    agents = list(agents_result.scalars().all())
    deployments_result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.user_id == user_id)
    )
    deployments_by_agent = {
        row.agent_id: row for row in deployments_result.scalars().all()
    }

    candidates: list[AgentDeploymentCandidateResponse] = []
    for agent in agents:
        deployment = deployments_by_agent.get(agent.id)
        eligible = agent.identity_mode == AGENT_IDENTITY_FIXED
        candidates.append(
            AgentDeploymentCandidateResponse(
                agent_id=agent.id,
                agent_name=agent.name,
                runtime_name=agent.runtime_name,
                existing_deployment_id=deployment.id if deployment else None,
                existing_public_id=deployment.public_id if deployment else None,
                eligible=eligible,
                ineligible_reason=None,
                ineligible_reason_code=None if eligible else FIXED_IDENTITY_REASON_CODE,
            )
        )
    return candidates


async def create_deployment(
    db: AsyncSession, user_id: uuid.UUID, data: AgentDeploymentCreate
) -> AgentDeployment:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == data.agent_id, Agent.user_id == user_id)
        .options(selectinload(Agent.model))
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise NotFoundError("AGENT_NOT_FOUND", "agent not found")
    if agent.identity_mode != AGENT_IDENTITY_FIXED:
        raise ValidationError(
            "AGENT_API_FIXED_IDENTITY_REQUIRED",
            "API deployment requires fixed identity.",
        )

    existing = await db.execute(
        select(AgentDeployment)
        .where(AgentDeployment.agent_id == agent.id, AgentDeployment.user_id == user_id)
        .options(selectinload(AgentDeployment.agent))
    )
    existing_row = existing.scalar_one_or_none()
    if existing_row is not None:
        return existing_row

    public_id = agent.runtime_name or f"agent_{agent.id.hex[:12]}"
    row = AgentDeployment(
        agent_id=agent.id,
        user_id=user_id,
        public_id=public_id,
        allow_streaming=data.allow_streaming,
        allow_background=data.allow_background,
        rate_limit_per_minute=data.rate_limit_per_minute,
        daily_token_limit=data.daily_token_limit,
    )
    db.add(row)
    await db.commit()
    result = await db.execute(
        select(AgentDeployment)
        .where(AgentDeployment.id == row.id)
        .options(selectinload(AgentDeployment.agent))
    )
    return result.scalar_one()


async def update_deployment(
    db: AsyncSession,
    user_id: uuid.UUID,
    deployment_id: uuid.UUID,
    data: AgentDeploymentUpdate,
) -> AgentDeployment:
    result = await db.execute(
        select(AgentDeployment)
        .where(AgentDeployment.id == deployment_id, AgentDeployment.user_id == user_id)
        .options(selectinload(AgentDeployment.agent))
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")
    if data.status is not None:
        row.status = data.status
    if data.allow_streaming is not None:
        row.allow_streaming = data.allow_streaming
    if data.allow_background is not None:
        row.allow_background = data.allow_background
    if data.rate_limit_per_minute is not None:
        row.rate_limit_per_minute = data.rate_limit_per_minute
    if data.daily_token_limit is not None:
        row.daily_token_limit = data.daily_token_limit
    await db.commit()
    await db.refresh(row)
    return row


async def _load_deployments_for_key(
    db: AsyncSession, user_id: uuid.UUID, deployment_ids: list[uuid.UUID]
) -> list[AgentDeployment]:
    if not deployment_ids:
        return []
    result = await db.execute(
        select(AgentDeployment)
        .where(AgentDeployment.user_id == user_id, AgentDeployment.id.in_(deployment_ids))
        .options(selectinload(AgentDeployment.agent))
    )
    rows = list(result.scalars().all())
    if len({row.id for row in rows}) != len(set(deployment_ids)):
        raise ValidationError(
            "AGENT_API_DEPLOYMENT_NOT_FOUND",
            "one or more deployments are unavailable",
        )
    return rows


async def create_api_key(
    db: AsyncSession, user_id: uuid.UUID, data: AgentApiKeyCreate
) -> tuple[AgentApiKey, str]:
    if not data.allow_all_deployments and not data.deployment_ids:
        raise ValidationError(
            "AGENT_API_KEY_DEPLOYMENT_REQUIRED",
            "select at least one deployment or allow all deployments",
        )
    deployments = await _load_deployments_for_key(db, user_id, data.deployment_ids)
    generated = generate_api_key()
    row = AgentApiKey(
        user_id=user_id,
        name=data.name,
        description=data.description,
        key_id=generated.key_id,
        key_hash=generated.secret_hash,
        prefix=generated.prefix,
        last_four=generated.last_four,
        scopes=list(dict.fromkeys(data.scopes)),
        allow_all_deployments=data.allow_all_deployments,
        expires_at=expires_at_from_days(data.expires_in_days),
    )
    for deployment in deployments:
        row.deployment_links.append(AgentApiKeyDeployment(deployment_id=deployment.id))
    db.add(row)
    await db.commit()
    result = await db.execute(
        select(AgentApiKey).where(AgentApiKey.id == row.id).options(_key_options())
    )
    return result.scalar_one(), generated.cleartext


async def list_api_keys(db: AsyncSession, user_id: uuid.UUID) -> list[AgentApiKey]:
    result = await db.execute(
        select(AgentApiKey)
        .where(AgentApiKey.user_id == user_id)
        .options(_key_options())
        .order_by(AgentApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(
    db: AsyncSession, user_id: uuid.UUID, key_id: uuid.UUID
) -> AgentApiKey:
    result = await db.execute(
        select(AgentApiKey)
        .where(AgentApiKey.id == key_id, AgentApiKey.user_id == user_id)
        .options(_key_options())
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("AGENT_API_KEY_NOT_FOUND", "api key not found")
    if row.revoked_at is None:
        row.revoked_at = utc_now_naive()
        await db.commit()
    result = await db.execute(
        select(AgentApiKey).where(AgentApiKey.id == row.id).options(_key_options())
    )
    return result.scalar_one()
