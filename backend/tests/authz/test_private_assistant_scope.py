"""Assistant tools use the selected target scope and recheck detached actor state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.authz.builder_scope import resolve_builder_actor
from app.models.user import User
from app.services.builder_service import create_session
from app.services.tool_service import get_tools_catalog

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_assistant_target_lookup_is_private_to_the_exact_org(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    agent_id = collector_case.conversation.agent_id
    assert agent_id is not None
    assert await get_agent_with_eager_load(db, agent_id, private_scopes.first) is not None
    assert await get_agent_with_eager_load(db, agent_id, private_scopes.second) is None


async def test_assistant_target_rechecks_disabled_user(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    agent_id = collector_case.conversation.agent_id
    assert agent_id is not None
    await db.execute(
        update(User).where(User.id == private_scopes.first.user_id).values(is_active=False)
    )
    await db.commit()
    assert await get_agent_with_eager_load(db, agent_id, private_scopes.first) is None


async def test_assistant_builder_tool_catalog_does_not_cross_companies(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    first = await get_tools_catalog(db, private_scopes.first)
    assert any(item["name"] == "Collector tool fixture" for item in first)
    second = await get_tools_catalog(db, private_scopes.second)
    assert all(item["name"] != "Collector tool fixture" for item in second)


async def test_builder_background_scope_ignores_saved_company_preference(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    session = await create_session(db, private_scopes.first, "Scoped builder fixture")
    await db.execute(
        update(User)
        .where(User.id == private_scopes.first.user_id)
        .values(last_active_org_id=private_scopes.second.org_id)
    )
    await db.commit()
    context = await resolve_builder_actor(db, session.id, private_scopes.first.user_id)
    assert (context.org_id, context.tenant_id) == (
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )
