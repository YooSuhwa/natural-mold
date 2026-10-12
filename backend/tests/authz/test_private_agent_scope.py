"""Actual Agent CRUD must remain private after the same owner switches companies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.models.agent import Agent
from app.models.model import Model

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class AgentCase:
    client: AsyncClient
    agent_id: UUID
    model_id: UUID


@pytest.fixture
async def agent_case(
    db: AsyncSession, raw_client: AsyncClient, private_scopes: PrivateScopes
) -> AgentCase:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.first.org_id))
    csrf = raw_client.cookies.get("moldy_csrf")
    assert csrf is not None
    response = await raw_client.post(
        "/api/agents",
        json={
            "name": "Private agent fixture",
            "system_prompt": "Fixture",
            "model_id": str(model.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return AgentCase(raw_client, UUID(response.json()["id"]), model.id)


async def test_agent_writer_persists_declared_owner_org_and_company(
    db: AsyncSession, agent_case: AgentCase, private_scopes: PrivateScopes
) -> None:
    agent = await db.get(Agent, agent_case.agent_id)
    assert agent is not None
    assert (agent.user_id, agent.org_id, agent.tenant_id) == (
        private_scopes.first.user_id,
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )


@pytest.mark.parametrize("suffix", ["", "/summary"])
async def test_agent_lists_exclude_same_owner_foreign_company(
    agent_case: AgentCase, private_scopes: PrivateScopes, suffix: str
) -> None:
    own = await agent_case.client.get(f"/api/agents{suffix}")
    assert own.status_code == 200, own.text
    assert [row["id"] for row in own.json()] == [str(agent_case.agent_id)]
    agent_case.client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.second.org_id))
    foreign = await agent_case.client.get(f"/api/agents{suffix}")
    assert foreign.status_code == 200, foreign.text
    assert foreign.json() == []


@pytest.mark.parametrize("operation", ["get", "update", "favorite", "delete"])
async def test_agent_id_operations_deny_same_owner_foreign_company(
    agent_case: AgentCase,
    private_scopes: PrivateScopes,
    operation: Literal["get", "update", "favorite", "delete"],
) -> None:
    agent_case.client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.second.org_id))
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    headers = {"X-CSRF-Token": csrf}
    endpoint = f"/api/agents/{agent_case.agent_id}"
    match operation:
        case "get":
            response = await agent_case.client.get(endpoint)
        case "update":
            response = await agent_case.client.put(
                endpoint, json={"name": "Forbidden"}, headers=headers
            )
        case "favorite":
            response = await agent_case.client.patch(f"{endpoint}/favorite", headers=headers)
        case "delete":
            response = await agent_case.client.delete(endpoint, headers=headers)
    assert response.status_code == 404, response.text


async def test_agent_delete_foreign_scope_matches_missing_target(
    agent_case: AgentCase,
    private_scopes: PrivateScopes,
) -> None:
    # Given an authenticated owner whose selected company differs from the target Agent.
    agent_case.client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.second.org_id))
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    headers = {"X-CSRF-Token": csrf}

    # When the owner deletes the foreign-scope target and an absent target.
    foreign = await agent_case.client.delete(f"/api/agents/{agent_case.agent_id}", headers=headers)
    missing = await agent_case.client.delete(f"/api/agents/{uuid4()}", headers=headers)

    # Then both remain enumeration-safe and indistinguishable.
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
