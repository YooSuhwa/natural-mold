"""Independent HTTP witness of current persisted API Thread parent closure."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.authz.agent_api_scope import private_deployment_predicate
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.routers import agent_runtime_api
from app.schemas.agent_api import AgentThreadCreateRequest

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("malformed", [False, True])
@pytest.mark.parametrize("preloaded", [False, True])
async def test_thread_http_authorizes_surviving_agent_parent(
    db: AsyncSession,
    api_case: ApiCase,
    private_scopes: PrivateScopes,
    malformed: bool,
    preloaded: bool,
) -> None:
    # Given: an actual authorized API Thread and an allow-all private key.
    own = private_scopes.first
    principal = ApiKeyPrincipal(api_case.key, own.user_id, own)
    thread = await runtime_service.create_thread(
        db,
        principal=principal,
        deployment=api_case.first,
        request=AgentThreadCreateRequest(agent_id=api_case.first.public_id),
    )
    thread_public_id = thread.public_id
    if malformed:
        await db.execute(
            update(Agent)
            .where(Agent.id == api_case.first.agent_id)
            .values(
                org_id=private_scopes.second.org_id,
                tenant_id=private_scopes.second.tenant_id,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    persisted = await db.execute(
        select(Agent.org_id, Agent.tenant_id).where(Agent.id == api_case.first.agent_id)
    )
    persisted_scope = persisted.one()
    expected_scope = private_scopes.second if malformed else own
    assert persisted_scope == (expected_scope.org_id, expected_scope.tenant_id)
    candidate = await db.scalar(
        select(AgentDeployment.id).where(
            AgentDeployment.id == api_case.first.id, private_deployment_predicate(own)
        )
    )
    assert (candidate is None) == malformed
    if not preloaded:
        db.expunge_all()

    # When: the real endpoint serializes the Thread through a fresh HTTP session.
    response = await api_case.client.get(
        f"/v1/threads/{thread_public_id}",
        headers={"Authorization": f"Bearer {api_case.cleartext}"},
    )

    # Then: malformed surviving private Agent parents must have missing semantics.
    assert response.status_code == (404 if malformed else 200), response.text


@pytest.mark.parametrize("surface", ["list", "wait"])
async def test_sibling_api_surface_blocks_same_foreign_agent_parent(
    db: AsyncSession,
    api_case: ApiCase,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
    surface: Literal["list", "wait"],
) -> None:
    # Given: the identical malformed Thread graph, with provider use forbidden.
    own = private_scopes.first
    principal = ApiKeyPrincipal(api_case.key, own.user_id, own)
    thread = await runtime_service.create_thread(
        db,
        principal=principal,
        deployment=api_case.first,
        request=AgentThreadCreateRequest(agent_id=api_case.first.public_id),
    )
    await db.execute(
        update(Agent)
        .where(Agent.id == api_case.first.agent_id)
        .values(
            org_id=private_scopes.second.org_id,
            tenant_id=private_scopes.second.tenant_id,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    provider = AsyncMock(side_effect=AssertionError("No provider effects authorized"))
    monkeypatch.setattr(agent_runtime_api, "execute_agent_invoke", provider)
    headers = {"Authorization": f"Bearer {api_case.cleartext}"}

    # When: one sibling surface handles this persisted malformed graph.
    match surface:
        case "list":
            response = await api_case.client.get("/v1/agents", headers=headers)
        case "wait":
            response = await api_case.client.post(
                f"/v1/threads/{thread.public_id}/runs/wait",
                headers=headers,
                json={
                    "agent_id": api_case.first.public_id,
                    "input": {"messages": [{"role": "user", "content": "Scoped control"}]},
                },
            )
        case unreachable:
            assert_never(unreachable)

    # Then: lists omit it, execution rejects it before a Run or provider call.
    match surface:
        case "list":
            assert response.status_code == 200
            assert response.json()["data"] == []
        case "wait":
            assert response.status_code == 404
        case unreachable:
            assert_never(unreachable)
    assert provider.await_count == 0
    assert await db.scalar(select(AgentApiRun.id)) is None
