"""Seeds for the E-01 share/access/transfer API tests (plan 9.7, 10.2, 10.7)."""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.credential import Credential
from app.models.model import Model
from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantMember
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.models.skill import Skill
from app.models.user import User
from tests.authz.d04_support import OTHER_ORG_ID, OTHER_USER_ID, add_tool, seed_scope
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID, TestSession

FOREIGN_USER_ID = uuid5(NAMESPACE_URL, "moldy/e01-foreign-user")
THIRD_USER_ID = uuid5(NAMESPACE_URL, "moldy/e01-third-member")
ME = f"user:{TEST_USER_ID}"


def base(kind: str, resource_id: object) -> str:
    return f"/api/resources/{kind}/{resource_id}"


async def seed(db: AsyncSession) -> None:
    """Test user + another member of the org; a user of the sibling org only."""
    await seed_scope(db)
    if await db.get(User, FOREIGN_USER_ID) is None:
        db.add(User(id=FOREIGN_USER_ID, email="foreign@test.com", name="Foreign"))
        await db.flush()
        db.add(TenantMember(tenant_id=TEST_TENANT_ID, user_id=FOREIGN_USER_ID))
        db.add(
            OrganizationMember(
                org_id=OTHER_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=FOREIGN_USER_ID
            )
        )
    await db.commit()


async def add_member(db: AsyncSession, user_id: UUID) -> None:
    actor = CurrentUser(id=user_id, email=f"{user_id}@t.io", name="Third", is_super_user=False)
    await ensure_unit_scope(db, actor)
    await db.commit()


async def add_agent(db: AsyncSession, *, owner: UUID = TEST_USER_ID) -> Agent:
    model = Model(provider="openai", model_name=f"m-{uuid4().hex[:6]}", display_name="M")
    db.add(model)
    await db.flush()
    agent = Agent(user_id=owner, name="A", system_prompt="p", model_id=model.id)
    agent.org_id, agent.tenant_id = TEST_ORG_ID, TEST_TENANT_ID
    db.add(agent)
    await db.commit()
    return agent


async def add_credential(
    db: AsyncSession,
    *,
    owner: UUID = TEST_USER_ID,
    scope: str = "personal",
    definition_key: str = "http_bearer",
) -> Credential:
    credential = Credential(
        user_id=owner if scope == "personal" else None,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        scope=scope,
        definition_key=definition_key,
        name="Slack",
        data_encrypted="x",
        key_id="k1",
    )
    db.add(credential)
    await db.commit()
    return credential


async def add_skill(db: AsyncSession, *, owner: UUID, slug: str) -> Skill:
    skill = Skill(user_id=owner, name=slug, slug=slug, description="d")
    skill.org_id, skill.tenant_id = TEST_ORG_ID, TEST_TENANT_ID
    db.add(skill)
    await db.commit()
    return skill


async def add_group(db: AsyncSession, *, org: UUID = TEST_ORG_ID, members: tuple = ()) -> UUID:
    group = Group(org_id=org, tenant_id=TEST_TENANT_ID, name=f"g-{uuid4().hex[:6]}")
    db.add(group)
    await db.flush()
    for user_id in members:
        db.add(
            GroupMember(group_id=group.id, user_id=user_id, org_id=org, tenant_id=TEST_TENANT_ID)
        )
    await db.commit()
    return group.id


async def make_admin(db: AsyncSession, user_id: UUID) -> None:
    db.add(
        OrganizationRoleGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            role="admin",
            principal_type="user",
            principal_id=user_id,
        )
    )
    await db.commit()


async def give_cap(db: AsyncSession, capability: str) -> None:
    db.add(
        OrgCapabilityGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            capability=capability,
            principal_type="user",
            principal_id=TEST_USER_ID,
        )
    )
    await db.commit()


async def grants(resource_id: UUID, *, state: str | None = None) -> list[ResourceGrant]:
    async with TestSession() as db:
        query = select(ResourceGrant).where(ResourceGrant.resource_id == resource_id)
        if state is not None:
            query = query.where(ResourceGrant.state == state)
        return list(await db.scalars(query.order_by(ResourceGrant.granted_at)))


async def audit_actions(target_id: UUID) -> list[str]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.target_id == str(target_id))
            .order_by(AuditEvent.created_at)
        )
        return list(rows)


async def audit_event(target_id: UUID, action: str) -> AuditEvent:
    async with TestSession() as db:
        found = await db.scalar(
            select(AuditEvent).where(
                AuditEvent.target_id == str(target_id), AuditEvent.action == action
            )
        )
        assert found is not None, action
        return found


async def outbox_count() -> int:
    async with TestSession() as db:
        return len(list(await db.scalars(select(AuthzOutbox.id))))


__all__ = ["OTHER_ORG_ID", "OTHER_USER_ID", "TEST_ORG_ID", "TEST_USER_ID", "add_tool"]
