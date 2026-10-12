"""Conversation deletion rechecks actors and refuses foreign child traversal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.services.chat.conversations import delete_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class DeletionScenario:
    row: Conversation
    context: OrgContext
    denied: bool
    child: Conversation | None


@pytest.fixture(params=["own", "foreign", "revoked", "foreign_child"])
async def deletion_scenario(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> DeletionScenario:
    own = private_scopes.first
    row = Conversation(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=None,
        agent_lifecycle_state="purged",
        agent_purged_at=datetime.now(UTC),
        agent_name_snapshot="Retained fixture",
        last_activity_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    child = None
    if request.param == "foreign_child":
        other = private_scopes.second
        child = Conversation(
            user_id=other.user_id,
            org_id=other.org_id,
            tenant_id=other.tenant_id,
            agent_id=None,
            agent_lifecycle_state="purged",
            agent_purged_at=datetime.now(UTC),
            agent_name_snapshot="Foreign fixture",
            last_activity_at=datetime.now(UTC),
            source="side_chat",
            side_chat_parent_id=row.id,
        )
        db.add(child)
    await db.commit()
    match request.param:
        case "own":
            return DeletionScenario(row, own, False, None)
        case "foreign":
            return DeletionScenario(row, private_scopes.second, True, None)
        case "revoked":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
            )
            await db.commit()
            return DeletionScenario(row, own, True, None)
        case "foreign_child":
            return DeletionScenario(row, own, True, child)
        case _:
            pytest.fail("Unknown deletion fixture")


async def test_delete_requires_current_actor_and_matching_side_child(
    db: AsyncSession, deletion_scenario: DeletionScenario
) -> None:
    # Given a cached private history; checkpoint and file effects stay in owned fixtures.
    row = deletion_scenario.row
    checkpoint_delete = AsyncMock()
    with (
        TemporaryDirectory(prefix="moldy-delete-scope-") as root,
        pytest.MonkeyPatch.context() as patches,
    ):
        patches.setattr("app.agent_runtime.runtime_config._DATA_DIR", Path(root))
        patches.setattr("app.agent_runtime.checkpointer.delete_thread", checkpoint_delete)
        # When a caller attempts direct service deletion after the scope changed.
        if deletion_scenario.denied:
            with pytest.raises(AppError) as rejected:
                await delete_conversation(db, row, deletion_scenario.context)
            assert rejected.value.status == 404
        else:
            await delete_conversation(db, row, deletion_scenario.context)
    row_id = row.id
    child_id = deletion_scenario.child.id if deletion_scenario.child is not None else None
    db.expunge_all()
    persisted = await db.get(Conversation, row_id)
    # Then rejected deletion changes neither the root nor an ungranted child/checkpoint.
    if deletion_scenario.denied:
        assert persisted is not None
        checkpoint_delete.assert_not_awaited()
        if child_id is not None:
            child = await db.get(Conversation, child_id)
            assert child is not None and child.source == "side_chat"
            assert child.side_chat_parent_id == row_id
    else:
        assert persisted is None
        checkpoint_delete.assert_awaited_once_with(str(row_id))
