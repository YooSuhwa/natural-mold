"""Native m83 deletion refuses revoked actors and foreign SET NULL child traversal."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from app.authz.org_context import resolve_org_context
from app.authz.private_actor import PersistedActor
from app.config import settings
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services.chat.conversations import delete_conversation
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["own", "revoked", "foreign_child"])
async def test_native_delete_stops_before_foreign_child_or_revoked_actor_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: Literal["own", "revoked", "foreign_child"],
) -> None:
    # Given a real private actor and independently migrated m83 owner-role database.
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    checkpoint_delete = AsyncMock()
    monkeypatch.setattr("app.agent_runtime.checkpointer.delete_thread", checkpoint_delete)
    monkeypatch.setattr("app.agent_runtime.runtime_config._DATA_DIR", tmp_path)
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-delete@example.com",
                "name": "Delete fixture",
                "password": "native-delete-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-delete@example.com"))
            assert user is not None and user.last_active_org_id is not None
            context = await resolve_org_context(
                db, PersistedActor(user.id, user.is_super_user), cookie=str(user.last_active_org_id)
            )
            parent = Conversation(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                agent_id=None,
                agent_name_snapshot="Retained fixture",
                last_activity_at=datetime.now(UTC),
            )
            db.add(parent)
            await db.commit()
            parent_id = parent.id
        child_id = None
        match kind:
            case "foreign_child":
                other = await select_second_company(stack, headers, "native-delete@example.com")
                async with stack.sessions() as db:
                    child = Conversation(
                        user_id=other.user_id,
                        org_id=other.org_id,
                        tenant_id=other.tenant_id,
                        agent_id=None,
                        agent_name_snapshot="Foreign retained fixture",
                        last_activity_at=datetime.now(UTC),
                        source="side_chat",
                        side_chat_parent_id=parent_id,
                    )
                    db.add(child)
                    await db.commit()
                    child_id = child.id
            case "revoked":
                async with stack.sessions() as db:
                    await db.execute(
                        delete(OrganizationMember).where(
                            OrganizationMember.org_id == context.org_id,
                            OrganizationMember.user_id == context.user_id,
                        )
                    )
                    await db.commit()
            case "own":
                pass
            case unreachable:
                assert_never(unreachable)
        denied = kind != "own"
        # When direct service deletion receives the persisted context from before the change.
        async with stack.sessions() as db:
            parent = await db.get(Conversation, parent_id)
            assert parent is not None
            if not denied:
                await delete_conversation(db, parent, context)
                await db.commit()
            else:
                with pytest.raises(AppError) as rejected:
                    await delete_conversation(db, parent, context)
                assert rejected.value.status == 404
                await db.rollback()
        # Then physical FK effects and checkpoint cleanup occur only for an active own root.
        async with stack.sessions() as db:
            parent = await db.get(Conversation, parent_id)
            if not denied:
                assert parent is None
                checkpoint_delete.assert_awaited_once_with(str(parent_id))
            else:
                assert parent is not None
                checkpoint_delete.assert_not_awaited()
            if child_id is not None:
                child = await db.get(Conversation, child_id)
                assert child is not None and child.side_chat_parent_id == parent_id
                assert child.source == "side_chat"
