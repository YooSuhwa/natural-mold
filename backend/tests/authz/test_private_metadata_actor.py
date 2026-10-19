"""Independent captured-actor private metadata boundary witness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import conversation_scope
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.services import agent_service
from app.services.chat import conversations

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
Action = Literal["conversation_load", "conversation_list", "agent_list", "agent_summary"]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize(
    "action", ["conversation_load", "conversation_list", "agent_list", "agent_summary"]
)
async def test_private_metadata_read_revalidates_captured_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    action: Action,
    revoked: bool,
) -> None:
    own = private_scopes.first
    row = await conversations.create_conversation(db, api_case.first.agent_id, context=own)
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    disclosed = False
    try:
        match action:
            case "conversation_load":
                loaded = await conversation_scope.load_private_conversation(db, row.id, own)
                disclosed = loaded is not None and loaded.id == row.id
            case "conversation_list":
                rows = await conversations.list_conversations(db, api_case.first.agent_id, own)
                disclosed = any(item.id == row.id for item in rows)
            case "agent_list":
                agents = await agent_service.list_agents(db, own)
                disclosed = any(item.id == api_case.first.agent_id for item in agents)
            case "agent_summary":
                summaries = await agent_service.list_agent_summaries(db, own)
                disclosed = any(item["id"] == api_case.first.agent_id for item in summaries)
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        if error.status not in {403, 404}:
            raise
    assert disclosed != revoked


@pytest.mark.parametrize("revoked", [False, True])
async def test_guarded_private_loader_and_fresh_http_controls(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    revoked: bool,
) -> None:
    own = private_scopes.first
    row = await conversations.create_conversation(db, api_case.first.agent_id, context=own)
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    denied = False
    try:
        loaded = await conversation_scope.require_private_conversation(db, row.id, own)
        assert loaded.id == row.id
    except AppError as error:
        denied = error.status in {403, 404}
    response = await api_case.client.get(
        "/v1/agents", headers={"Authorization": f"Bearer {api_case.cleartext}"}
    )
    assert denied == revoked
    assert response.status_code == (401 if revoked else 200)
