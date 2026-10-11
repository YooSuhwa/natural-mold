"""Native m83 side-chat creation and policy snapshots keep the selected company."""

from uuid import UUID

import pytest

from app.config import settings
from app.models.conversation import Conversation
from app.models.model import Model
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_side_chat_policy_and_promotion_keep_parent_scope(
    monkeypatch: pytest.MonkeyPatch, other_company: bool
) -> None:
    # Given a real JWT actor in the default or explicitly selected second company.
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-side@example.com",
                "name": "Side fixture",
                "password": "native-side-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-side@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="side-fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Side fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        parent_response = await stack.client.post(
            f"/api/agents/{agent.json()['id']}/conversations",
            headers=headers,
            json={},
        )
        assert parent_response.status_code == 201, parent_response.text
        parent_id = UUID(parent_response.json()["id"])
        # When actual HTTP creation, reuse and promotion exercise the scoped policy service.
        side_response = await stack.client.post(
            f"/api/conversations/{parent_id}/side-chats",
            headers=headers,
        )
        assert side_response.status_code == 201, side_response.text
        side_id = UUID(side_response.json()["id"])
        reopened = await stack.client.post(
            f"/api/conversations/{parent_id}/side-chats",
            headers=headers,
        )
        assert reopened.status_code == 200 and reopened.json()["id"] == str(side_id)
        promoted = await stack.client.post(
            f"/api/conversations/{side_id}/side-chats/save",
            headers=headers,
        )
        assert promoted.status_code == 200, promoted.text
        # Then the physical side row and immutable policy tuple match the persisted parent.
        async with stack.sessions() as db:
            parent = await db.get(Conversation, parent_id)
            side = await db.get(Conversation, side_id)
            assert parent is not None and side is not None
            assert (side.user_id, side.org_id, side.tenant_id) == (
                parent.user_id,
                parent.org_id,
                parent.tenant_id,
            )
            assert side.side_chat_parent_id == parent_id and side.source == "ui"
            assert parent.runtime_policy_snapshot is not None
            assert side.runtime_policy_snapshot == parent.runtime_policy_snapshot
            assert side.runtime_policy_hash == parent.runtime_policy_hash
