"""Independent actual public-service denial and legitimate mutation witnesses."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service
from app.exceptions import AppError
from app.marketplace import agent_spec
from app.marketplace.schemas import PublishAgentIn
from app.models.agent import Agent
from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate, AgentDeploymentUpdate

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
type Action = Literal["create_deployment", "update_deployment", "create_key", "publish_agent"]


def test_production_provenance() -> None:
    expected = Path(__file__).resolve().parents[2]
    assert Path(service.__file__).is_relative_to(expected)
    assert Path(agent_spec.__file__).is_relative_to(expected)


@pytest.mark.parametrize(
    "action", ["create_deployment", "update_deployment", "create_key", "publish_agent"]
)
@pytest.mark.parametrize("missing", [False, True])
async def test_private_target_denial_has_no_sql_write(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    org_fixture: OrgFixture,
    action: Action,
    missing: bool,
) -> None:
    # Given an active captured owner and pending private content unrelated to the denied target.
    pending = await db.get(Agent, api_case.second.agent_id)
    assert pending is not None
    pending_id, original = pending.id, pending.name
    target_id = uuid4() if missing else api_case.second.id
    agent_id = uuid4() if missing else pending.id
    pending.name = "F2 R17 pending private content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    # When the real public production service receives a foreign or missing target.
    try:
        match action:
            case "create_deployment":
                await service.create_deployment(
                    db, private_scopes.first, AgentDeploymentCreate(agent_id=agent_id)
                )
            case "update_deployment":
                await service.update_deployment(
                    db, private_scopes.first, target_id, AgentDeploymentUpdate(status="disabled")
                )
            case "create_key":
                await service.create_api_key(
                    db,
                    private_scopes.first,
                    AgentApiKeyCreate(
                        name="F2 rejected key", deployment_ids=[target_id], scopes=["read"]
                    ),
                )
            case "publish_agent":
                await agent_spec.publish_agent(
                    db,
                    agent_id=agent_id,
                    user=org_fixture.user,
                    context=private_scopes.first,
                    body=PublishAgentIn(name="F2 rejected publication", visibility="private"),
                )
            case unreachable:
                assert_never(unreachable)
    except AppError:
        denied = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == pending_id))
    staged = pending in db.dirty
    await db.rollback()
    # Then reject before any SQL mutation; rollback bounds every adverse witness.
    assert denied
    assert flush.call_count == 0
    assert stored == original
    assert staged


async def test_authorized_mutation_control(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase
) -> None:
    # Given a legitimate owned Deployment and its captured active organization.
    own = private_scopes.first
    # When ordinary owner updates and creates an exact-bound Key.
    row = await service.update_deployment(
        db, own, api_case.first.id, AgentDeploymentUpdate(allow_background=True)
    )
    key, cleartext = await service.create_api_key(
        db,
        own,
        AgentApiKeyCreate(name="F2 legitimate key", deployment_ids=[row.id], scopes=["read"]),
    )
    # Then ordinary commit behavior and exact child linkage survive the cutover.
    await db.refresh(row)
    assert row.allow_background
    assert cleartext
    assert (key.user_id, key.org_id, key.tenant_id) == (own.user_id, own.org_id, own.tenant_id)
    assert [link.deployment_id for link in key.deployment_links] == [row.id]
