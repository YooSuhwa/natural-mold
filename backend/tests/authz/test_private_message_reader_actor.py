"""Checkpoint content readers authorize the current private target before source access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.services import thread_branch_service
from app.services.chat import conversations, messages

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@dataclass(frozen=True, slots=True)
class EmptyTree:
    nodes: tuple[()] = ()


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("foreign", [False, True])
async def test_message_reader_checks_private_actor_and_target_before_source(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    revoked: bool,
    foreign: bool,
) -> None:
    own = private_scopes.first
    row = await conversations.create_conversation(db, api_case.first.agent_id, context=own)
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id,
                OrganizationMember.org_id == own.org_id,
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    reader = Mock(return_value=None)
    tree = AsyncMock(return_value=EmptyTree())
    monkeypatch.setattr(checkpointer, "get_checkpointer", reader)
    monkeypatch.setattr(thread_branch_service, "build_message_tree", tree)
    context = private_scopes.second if foreign else own
    denied = False
    try:
        await messages.list_messages_from_checkpointer(db, row, context=context)
    except AppError as error:
        denied = error.status in {403, 404}
    assert denied == (revoked or foreign)
    assert reader.call_count == (0 if revoked or foreign else 1)
    assert tree.await_count == (0 if revoked or foreign else 1)
