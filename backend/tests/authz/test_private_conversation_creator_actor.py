"""Independent F1 current-actor witness for the public Conversation creator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services.chat import conversations

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("change", ["active", "membership", "account"])
@pytest.mark.parametrize("source", ["ui", "draft"])
async def test_conversation_creator_rechecks_captured_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    source: Literal["ui", "draft"],
    change: Literal["active", "membership", "account"],
) -> None:
    own = private_scopes.first
    if change == "membership":
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
    elif change == "account":
        await db.execute(
            update(User)
            .where(User.id == own.user_id)
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    before = await db.scalar(select(func.count()).select_from(Conversation))
    assert before is not None
    denied = False
    try:
        row = await conversations.create_conversation(
            db, api_case.first.agent_id, context=own, source=source
        )
        assert (row.user_id, row.org_id, row.tenant_id) == (own.user_id, own.org_id, own.tenant_id)
    except AppError as error:
        assert error.status in (403, 404)
        denied = True
    with db.no_autoflush:
        after = await db.scalar(select(func.count()).select_from(Conversation))
    await db.rollback()
    assert denied == (change != "active")
    assert after == before if change != "active" else after == before + 1
