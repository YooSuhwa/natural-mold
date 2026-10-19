"""Shared seed rows for the C-04 GrantService / outbox tests (SQLite, fake tuple store)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.authz import AuthzOutbox
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.organization import Organization, OrganizationMember, Tenant
from app.models.organization_group import Group
from tests.conftest import TestSession, make_user

START = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float = 1) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


@dataclass(frozen=True, slots=True)
class World:
    ctx: OrgContext
    other_ctx: OrgContext  # second organization of the same company
    member: UUID
    outsider: UUID  # active user of the other organization only
    group: UUID
    agent: UUID
    tenant: UUID
    model: UUID


async def _org(db: AsyncSession, tenant: UUID, *users: UUID) -> UUID:
    org = Organization(tenant_id=tenant, name="o", slug=f"o-{uuid4()}")
    db.add(org)
    await db.flush()
    for user in users:
        db.add(OrganizationMember(org_id=org.id, tenant_id=tenant, user_id=user))
    return org.id


async def seed_world(db: AsyncSession) -> World:
    actor, member, outsider = [(await make_user(db)).id for _ in range(3)]
    tenant = Tenant(name=f"t-{uuid4()}", slug=f"t-{uuid4()}", secrets_namespace=f"t-{uuid4()}")
    db.add(tenant)
    await db.flush()
    org = await _org(db, tenant.id, actor, member)
    other = await _org(db, tenant.id, actor, outsider)
    model = Model(provider="openai", model_name=f"m-{uuid4()}", display_name="m")
    group = Group(org_id=org, tenant_id=tenant.id, name="g")
    db.add_all([model, group])
    await db.flush()
    agent = Agent(name="a", system_prompt="p", user_id=actor, model_id=model.id)
    agent.org_id, agent.tenant_id = org, tenant.id
    db.add(agent)
    await db.commit()
    return World(
        ctx=OrgContext(org_id=org, tenant_id=tenant.id, user_id=actor),
        other_ctx=OrgContext(org_id=other, tenant_id=tenant.id, user_id=actor),
        member=member,
        outsider=outsider,
        group=group.id,
        agent=agent.id,
        tenant=tenant.id,
        model=model.id,
    )


async def system_item(db: AsyncSession) -> UUID:
    item = MarketplaceItem(
        resource_type="skill", is_system=True, name="s", slug=f"s-{uuid4()}", visibility="public"
    )
    db.add(item)
    await db.commit()
    return item.id


async def agent_target(
    db: AsyncSession, world: World, *, ctx: OrgContext | None = None, archived: bool = False
) -> GrantTarget:
    """A fresh committed agent of ``ctx`` (default: the first organization)."""
    scope = ctx or world.ctx
    agent = Agent(name="t", system_prompt="p", user_id=scope.user_id, model_id=world.model)
    agent.org_id, agent.tenant_id = scope.org_id, scope.tenant_id
    agent.archived_at = START if archived else None
    db.add(agent)
    await db.commit()
    return GrantTarget("agent", agent.id)


async def mcp_target(db: AsyncSession, world: World) -> GrantTarget:
    server = McpServer(user_id=world.ctx.user_id, name="m", transport="sse", url="https://x.test")
    server.org_id, server.tenant_id = world.ctx.org_id, world.tenant
    db.add(server)
    await db.commit()
    return GrantTarget("mcp_server", server.id)


def service(store: FakeTupleStore, clock: Clock) -> GrantService:
    return GrantService(store, TestSession, clock=clock)


async def outbox_rows(db: AsyncSession) -> list[AuthzOutbox]:
    found = await db.scalars(
        select(AuthzOutbox).order_by(AuthzOutbox.seq).execution_options(populate_existing=True)
    )
    return list(found)


async def count(db: AsyncSession, model: type) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)
