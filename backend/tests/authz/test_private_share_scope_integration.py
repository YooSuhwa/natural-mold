"""The m83 share mapper uses created_by as its physical owner without an extra column."""

from uuid import UUID

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.model import Model
from app.models.share_link import ShareLink
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_share_writer_persists_scope_without_owner_alias_column(
    monkeypatch: pytest.MonkeyPatch,
    other_company: bool,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-share@example.com",
                "name": "Share fixture",
                "password": "native-share-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-share@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Share fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        conversation = await stack.client.post(
            f"/api/agents/{agent.json()['id']}/conversations",
            headers=headers,
            json={"title": "Share fixture"},
        )
        assert conversation.status_code == 201, conversation.text
        shared = await stack.client.post(
            f"/api/conversations/{conversation.json()['id']}/share", headers=headers
        )
        assert shared.status_code == 200, shared.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-share@example.com"))
            link = await db.scalar(
                select(ShareLink).where(
                    ShareLink.conversation_id == UUID(conversation.json()["id"])
                )
            )
            assert user is not None and link is not None
            assert link.user_id == link.created_by == user.id
            assert link.org_id == user.last_active_org_id
            assert link.tenant_id is not None
            assert "user_id" not in ShareLink.__table__.columns
