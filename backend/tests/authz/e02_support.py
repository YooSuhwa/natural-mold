"""Seeds for the E-02 member-removal and account-deletion tests (plan 10.7, S15)."""

from uuid import UUID, uuid4

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    THIRD_USER_ID,
    add_member,
    make_admin,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

MEMBERS = f"/api/orgs/{TEST_ORG_ID}/members"
SCOPE = {"org_id": TEST_ORG_ID, "tenant_id": TEST_TENANT_ID}


async def seed_admin(db: AsyncSession) -> None:
    """The test user administers the org; OTHER leaves, THIRD receives ownership."""
    await seed(db)
    await make_admin(db, TEST_USER_ID)
    await add_member(db, THIRD_USER_ID)


async def version(client: AsyncClient) -> str:
    listed = await client.get(MEMBERS)
    assert listed.status_code == 200, listed.text
    return listed.headers["etag"]


async def remove(
    client: AsyncClient, body: dict[str, object], user: UUID = OTHER_USER_ID
) -> Response:
    headers = {"If-Match": await version(client)}
    return await client.post(f"{MEMBERS}/{user}/removal", json=body, headers=headers)


async def delete(client: AsyncClient, user: UUID = OTHER_USER_ID) -> Response:
    headers = {"If-Match": await version(client)}
    return await client.delete(f"{MEMBERS}/{user}", headers=headers)


async def add_entry_points(
    db: AsyncSession, agent: Agent, owner: UUID = OTHER_USER_ID
) -> tuple[AgentTrigger, AgentDeployment, AgentApiKey]:
    """An active trigger, Agent API deployment and key on ``agent``, owned by ``owner``."""
    scope = {**SCOPE, "user_id": owner}
    trigger = AgentTrigger(
        **scope,
        agent_id=agent.id,
        trigger_type="cron",
        schedule_config={"cron": "0 9 * * *"},
        input_message="run",
        status="active",
    )
    suffix = uuid4().hex[:8]
    deployment = AgentDeployment(**scope, agent_id=agent.id, public_id=f"e02-{suffix}")
    key = AgentApiKey(
        **scope,
        name="k",
        key_id=suffix,
        key_hash="b" * 64,
        prefix=f"e02-{suffix}",
        last_four="0000",
    )
    db.add_all([trigger, deployment, key])
    await db.flush()
    db.add(AgentApiKeyDeployment(api_key_id=key.id, deployment_id=deployment.id))
    await db.commit()
    return trigger, deployment, key
