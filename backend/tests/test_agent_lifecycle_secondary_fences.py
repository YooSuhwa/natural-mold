"""B08 Task 12 secondary Agent capability lifecycle fences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service as agent_api_service
from app.exceptions import AppError
from app.marketplace import agent_spec
from app.marketplace.schemas import CreateAgentFromBlueprintIn, PublishAgentIn
from app.models.agent import Agent
from app.models.agent_api import AgentDeployment
from app.models.agent_blueprint import AgentBlueprint
from app.models.agent_subagent import AgentSubAgentLink
from app.models.marketplace import MarketplaceVersion
from app.models.model import Model
from app.schemas.agent import AgentCreate, AgentUpdate
from app.schemas.agent_api import AgentDeploymentCreate, AgentDeploymentUpdate
from app.services import (
    agent_blueprint_graph,
    agent_service,
)
from app.services.agent_blueprint_service import create_agent_from_blueprint
from tests.authz.blueprint_preflight_fixture import blueprint, materialization_sql, new_tool
from tests.test_marketplace_agent_blueprint import _ensure_test_user, _make_agent_item

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = [
    "tests.authz.private_scope_fixture",
    "tests.authz.private_mcp_apps_fixture",
]


@dataclass(frozen=True, slots=True)
class SecondaryFenceCase:
    db: AsyncSession
    private_scopes: PrivateScopes


@pytest.fixture
def secondary_fence_case(
    db: AsyncSession,
    private_scopes: PrivateScopes,
) -> SecondaryFenceCase:
    return SecondaryFenceCase(db, private_scopes)


async def _agent(
    case: SecondaryFenceCase,
    *,
    name: str,
    archived: bool,
) -> Agent:
    db = case.db
    private_scopes = case.private_scopes
    model = await db.scalar(select(Model).limit(1))
    if model is None:
        model = Model(provider="openai", model_name="task-12", display_name="Task 12")
        db.add(model)
        await db.flush()
    agent = await agent_service.create_agent(
        db,
        AgentCreate(
            name=name,
            system_prompt="Task 12 fixture",
            model_id=model.id,
            identity_mode="fixed",
        ),
        private_scopes.first,
    )
    if archived:
        agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
        agent.archived_by = private_scopes.first.user_id
    await db.commit()
    return agent


async def test_archived_deployment_and_publication_deny_before_writes(
    secondary_fence_case: SecondaryFenceCase,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = secondary_fence_case
    db = case.db
    private_scopes = case.private_scopes
    archived = await _agent(case, name="Task 12 denied", archived=True)
    fallback_loader = AsyncMock()
    monkeypatch.setattr(agent_spec, "_load_fallback_models", fallback_loader)

    with pytest.raises(AppError) as deployment_error:
        await agent_api_service.create_deployment(
            db,
            private_scopes.first,
            AgentDeploymentCreate(agent_id=archived.id),
        )
    with pytest.raises(AppError) as publication_error:
        await agent_spec.publish_agent(
            db,
            agent_id=archived.id,
            user=org_fixture.user,
            context=private_scopes.first,
            body=PublishAgentIn(name="Task 12 denied publication", visibility="private"),
        )

    assert deployment_error.value.code == "AGENT_ARCHIVED"
    assert publication_error.value.code == "AGENT_ARCHIVED"
    fallback_loader.assert_not_awaited()
    assert await db.scalar(select(func.count()).select_from(AgentDeployment)) == 0
    assert await db.scalar(select(func.count()).select_from(MarketplaceVersion)) == 0


async def test_archived_deployment_cannot_be_reactivated(
    secondary_fence_case: SecondaryFenceCase,
) -> None:
    case = secondary_fence_case
    db = case.db
    private_scopes = case.private_scopes
    agent = await _agent(case, name="Task 12 deployed", archived=False)
    deployment = await agent_api_service.create_deployment(
        db,
        private_scopes.first,
        AgentDeploymentCreate(agent_id=agent.id),
    )
    await agent_api_service.update_deployment(
        db,
        private_scopes.first,
        deployment.id,
        AgentDeploymentUpdate(status="disabled"),
    )
    await db.commit()
    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    agent.archived_by = private_scopes.first.user_id
    await db.commit()

    with pytest.raises(AppError) as caught:
        await agent_api_service.update_deployment(
            db,
            private_scopes.first,
            deployment.id,
            AgentDeploymentUpdate(status="active"),
        )

    assert caught.value.code == "AGENT_ARCHIVED"
    await db.refresh(deployment)
    assert deployment.status == "disabled"


@pytest.mark.parametrize("archived", [False, True], ids=["active", "archived"])
async def test_delegation_writer_rejects_archived_child(
    secondary_fence_case: SecondaryFenceCase,
    archived: bool,
) -> None:
    case = secondary_fence_case
    db = case.db
    private_scopes = case.private_scopes
    parent = await _agent(case, name=f"Task 12 parent {archived}", archived=False)
    child = await _agent(case, name=f"Task 12 child {archived}", archived=archived)

    if archived:
        with pytest.raises(AppError) as caught:
            await agent_service.update_agent(
                db,
                parent,
                AgentUpdate(sub_agent_ids=[child.id]),
                context=private_scopes.first,
            )
        assert caught.value.code == "AGENT_ARCHIVED"
    else:
        await agent_service.update_agent(
            db,
            parent,
            AgentUpdate(sub_agent_ids=[child.id]),
            context=private_scopes.first,
        )
    linked = await db.scalar(
        select(func.count())
        .select_from(AgentSubAgentLink)
        .where(AgentSubAgentLink.parent_agent_id == parent.id)
    )
    assert linked == (0 if archived else 1)


@pytest.mark.parametrize("archived", [False, True], ids=["active", "archived"])
async def test_blueprint_materialization_fences_child_before_planning_effects(
    secondary_fence_case: SecondaryFenceCase,
    monkeypatch: pytest.MonkeyPatch,
    archived: bool,
) -> None:
    case = secondary_fence_case
    db = case.db
    private_scopes = case.private_scopes
    child = await _agent(
        case,
        name=f"Task 12 blueprint child {archived}",
        archived=archived,
    )
    root = blueprint(
        private_scopes.first,
        {
            "tools": new_tool(),
            "subagents": [{"name": child.name}],
        },
    )
    db.add(root)
    await db.commit()
    plan_tools = AsyncMock(wraps=agent_blueprint_graph.plan_tools)
    monkeypatch.setattr(agent_blueprint_graph, "plan_tools", plan_tools)
    before = await db.scalar(select(func.count()).select_from(Agent))

    with materialization_sql(db) as dml:
        if archived:
            with pytest.raises(AppError) as caught:
                await create_agent_from_blueprint(
                    db,
                    blueprint_id=root.id,
                    context=private_scopes.first,
                    body=CreateAgentFromBlueprintIn(model_id=child.model_id),
                )
            assert caught.value.code == "AGENT_ARCHIVED"
        else:
            created = await create_agent_from_blueprint(
                db,
                blueprint_id=root.id,
                context=private_scopes.first,
                body=CreateAgentFromBlueprintIn(model_id=child.model_id),
            )
            assert {link.sub_agent_id for link in created.sub_agent_links} == {child.id}

    if archived:
        plan_tools.assert_not_awaited()
        assert dml == []
        assert not db.new and not db.dirty
        assert await db.scalar(select(func.count()).select_from(Agent)) == before
    else:
        plan_tools.assert_awaited_once()
        assert dml


async def test_immutable_install_without_source_agent_remains_allowed(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    item, _version = await _make_agent_item(db)
    await db.commit()
    before = await db.scalar(select(func.count()).select_from(Agent))

    response = await client.post(f"/api/marketplace/items/{item.id}/install", json={})

    assert response.status_code == 201, response.text
    blueprint_id = response.json()["installed_agent_blueprint_id"]
    assert blueprint_id is not None
    assert await db.get(AgentBlueprint, UUID(blueprint_id)) is not None
    assert await db.scalar(select(func.count()).select_from(Agent)) == before
