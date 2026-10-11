# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
# Run via official wrapper, dedicated PYTHONPATH and -c backend/pyproject.toml -p tests.conftest.
"""Real JWT private API child projection witnesses with ordinary/operator controls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_api import AgentApiKeyDeployment, AgentDeployment
from app.models.user import User
from app.schemas.agent_api import AgentApiKeyCreate

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
Edge = Literal["deployment_agent", "key_deployment"]


@pytest.mark.parametrize("operator", [False, True])
@pytest.mark.parametrize("edge", ["deployment_agent", "key_deployment"])
async def test_http_api_children_keep_private_scope(
    db: AsyncSession,
    api_case: ApiCase,
    private_scopes: PrivateScopes,
    operator: bool,
    edge: Edge,
) -> None:
    own = private_scopes.first
    client = api_case.client
    client.cookies.set(ACTIVE_ORG_COOKIE, str(own.org_id))
    await db.execute(update(User).where(User.id == own.user_id).values(is_super_user=operator))
    marker = "F4_FOREIGN_PRIVATE_AGENT"
    await db.execute(update(Agent).where(Agent.id == api_case.second.agent_id).values(name=marker))
    key, _ = await service.create_api_key(
        db,
        own,
        AgentApiKeyCreate(
            name="Own linked fixture", scopes=["read"], deployment_ids=[api_case.first.id]
        ),
    )
    await db.commit()
    profile = await client.get("/api/auth/me")
    assert profile.status_code == 200 and profile.json()["is_super_user"] == operator
    path = "/api/agent-api/deployments" if edge == "deployment_agent" else "/api/agent-api/keys"
    control = await client.get(path)
    assert control.status_code == 200 and marker not in control.text
    if edge == "deployment_agent":
        # The foreign Agent must not already have a deployment (agent_id is unique).
        await db.execute(delete(AgentDeployment).where(AgentDeployment.id == api_case.second.id))
        await db.execute(
            update(AgentDeployment)
            .where(AgentDeployment.id == api_case.first.id)
            .values(agent_id=api_case.second.agent_id)
        )
    else:
        await db.execute(
            update(AgentApiKeyDeployment)
            .where(AgentApiKeyDeployment.api_key_id == key.id)
            .values(deployment_id=api_case.second.id)
        )
    await db.commit()
    result = await client.get(path)
    assert result.status_code == 200 and marker not in result.text


async def test_revoke_denies_key_with_foreign_deployment_before_effect(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes
) -> None:
    # Given an owned key whose persisted link points into another company.
    own = private_scopes.first
    key, _ = await service.create_api_key(
        db,
        own,
        AgentApiKeyCreate(name="Linked key", scopes=["read"], deployment_ids=[api_case.first.id]),
    )
    await db.execute(
        update(AgentApiKeyDeployment)
        .where(AgentApiKeyDeployment.api_key_id == key.id)
        .values(deployment_id=api_case.second.id)
    )
    await db.commit()
    # When the captured owner tries to revoke and serialize that key.
    with pytest.raises(AppError) as captured:
        await service.revoke_api_key(db, own, key.id)
    # Then the operation is hidden before changing the key.
    assert captured.value.status == 404
    await db.commit()
    await db.refresh(key)
    assert key.revoked_at is None
