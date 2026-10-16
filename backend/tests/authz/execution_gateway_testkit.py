"""Seed one tenant/org/user graph for execution gateway tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User


@dataclass(frozen=True, slots=True)
class GatewayGraph:
    context: OrgContext
    agent_id: UUID
    conversation_ids: tuple[UUID, ...]


async def seed_graph(db: AsyncSession, *, conversations: int = 2) -> GatewayGraph:
    key = uuid4().hex
    user = User(email=f"gateway-{key}@example.com", name="Gateway user", is_active=True)
    tenant = Tenant(name="Gateway company", slug=f"gw-{key}", secrets_namespace=f"gw/{key}")
    model = Model(provider="openai", model_name=f"gw-{key}", display_name="Gateway model")
    db.add_all((user, tenant, model))
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="Gateway org", slug=f"gw-{key}")
    db.add(organization)
    await db.flush()
    db.add_all(
        (
            TenantMember(tenant_id=tenant.id, user_id=user.id),
            OrganizationMember(org_id=organization.id, tenant_id=tenant.id, user_id=user.id),
        )
    )
    agent = Agent(
        user_id=user.id,
        org_id=organization.id,
        tenant_id=tenant.id,
        name="Gateway agent",
        model_id=model.id,
        system_prompt="prompt",
    )
    db.add(agent)
    await db.flush()
    rows = [
        Conversation(
            user_id=user.id,
            agent_id=agent.id,
            org_id=organization.id,
            tenant_id=tenant.id,
            title=f"c{index}",
            agent_name_snapshot=agent.name,
            last_activity_at=datetime.now(UTC),
        )
        for index in range(conversations)
    ]
    db.add_all(rows)
    await db.commit()
    context = OrgContext(org_id=organization.id, tenant_id=tenant.id, user_id=user.id)
    ordered = tuple(sorted((row.id for row in rows), key=lambda value: value.bytes))
    return GatewayGraph(context, agent.id, ordered)
