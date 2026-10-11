"""Agent metadata and republication decisions precede private SQL effects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.exceptions import AppError
from app.marketplace import agent_spec
from app.marketplace.schemas import PublishAgentIn
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.marketplace import MarketplaceItem
from app.models.skill import AgentSkillLink, Skill
from app.services import agent_service

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


def test_production_source_identity() -> None:
    source = Path(__file__).resolve().parents[2]
    assert Path(agent_service.__file__).resolve() == source / "app/services/agent_service.py"


@pytest.mark.parametrize("surface", ["detail", "list"])
@pytest.mark.parametrize("kind", ["skill", "subagent"])
async def test_private_agent_metadata_never_serializes_foreign_linked_content(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    surface: Literal["detail", "list"],
    kind: Literal["skill", "subagent"],
) -> None:
    # Given an authorized Agent with a persisted child in the same user's other company.
    own, foreign = private_scopes.first, private_scopes.second
    parent = await agent_service.get_agent(db, api_case.first.agent_id, own)
    assert parent is not None
    foreign_marker = "F2_PRIVATE_FOREIGN_COMPANY_METADATA"
    if kind == "skill":
        child = Skill(
            user_id=foreign.user_id,
            org_id=foreign.org_id,
            tenant_id=foreign.tenant_id,
            name=foreign_marker,
            slug="f2-foreign-child",
            kind="text",
            description="F2 foreign private description",
        )
        db.add(child)
        await db.flush()
        db.add(AgentSkillLink(agent_id=parent.id, skill_id=child.id))
    else:
        child_agent = await db.get(Agent, api_case.second.agent_id)
        assert child_agent is not None
        child_agent.name = foreign_marker
        db.add(
            AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=child_agent.id, position=0)
        )
    await db.commit()
    db.expunge_all()
    api_case.client.cookies.set(ACTIVE_ORG_COOKIE, str(own.org_id))
    # When the actual public HTTP metadata reader is called with company A context.
    endpoint = f"/api/agents/{api_case.first.agent_id}" if surface == "detail" else "/api/agents"
    response = await api_case.client.get(endpoint)
    # Then metadata from company B cannot be included in a successful company A response.
    leaked = foreign_marker in response.text
    await db.rollback()
    assert not leaked, response.text


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("missing", [False, True])
async def test_republication_denial_keeps_pending_sql_unflushed(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    org_fixture: OrgFixture,
    fallback: bool,
    missing: bool,
) -> None:
    # Given a valid owned Agent with optional catalog fallback and a foreign item target.
    own, foreign = private_scopes.first, private_scopes.second
    parent = await db.get(Agent, api_case.first.agent_id)
    pending = await db.get(Agent, api_case.second.agent_id)
    assert parent is not None and pending is not None
    parent.model_fallback_list = [str(parent.model_id)] if fallback else None
    item = MarketplaceItem(
        id=uuid4(),
        resource_type="agent",
        owner_user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        is_system=False,
        name="F2 foreign item",
        slug="f2-foreign-item",
        visibility="private",
        status="draft",
    )
    db.add(item)
    await db.commit()
    parent_id, pending_id, original = parent.id, pending.id, pending.name
    pending.name = "F2 pending unrelated private content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    # When the actual re-publication service receives a missing/foreign item ID.
    try:
        await agent_spec.publish_agent(
            db,
            agent_id=parent_id,
            user=org_fixture.user,
            context=own,
            body=PublishAgentIn(
                name="F2 rejected republication",
                visibility="private",
                item_id=uuid4() if missing else item.id,
            ),
        )
    except AppError as error:
        assert error.status == 404
        denied = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == pending_id))
    staged = pending in db.dirty
    await db.rollback()
    # Then complete target authorization precedes private SQL effects.
    assert denied
    assert flush.call_count == 0
    assert stored == original
    assert staged
