"""Actual m83 HTTP pagination cannot expose the same user's other company history."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User
from app.services import conversation_run_service as runs
from app.services.conversation_run_message_links import RunMessageLinkQuery, list_run_message_links
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_history_indexes_follow_selected_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-index@example.com",
                "name": "Index fixture",
                "password": "native-index-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Index fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        conversation = await stack.client.post(
            f"/api/agents/{agent.json()['id']}/conversations",
            headers=headers,
            json={"title": "Index fixture"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = UUID(conversation.json()["id"])
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-index@example.com"))
            assert user is not None
            context = await resolve_conversation_actor(db, conversation_id, user.id)
            run = await runs.create_run(
                db,
                conversation_id=conversation_id,
                agent_id=UUID(agent.json()["id"]),
                context=context,
                source="chat",
                input_preview="Native run metadata fixture",
            )
            message_id = str(uuid4())
            db.add(
                MessageEvent(
                    conversation_id=conversation_id,
                    assistant_msg_id=str(run.id),
                    linked_message_ids=[message_id],
                    events=[],
                )
            )
            await db.commit()
            run_id = run.id
        active = await stack.client.get(f"/api/conversations/{conversation_id}/runs/active")
        assert active.status_code == 200 and active.json()["id"] == str(run_id)
        links = await stack.client.get(
            f"/api/conversations/{conversation_id}/run-message-links",
            params={"message_id": message_id},
        )
        assert links.status_code == 200 and links.json() == [
            {"message_id": message_id, "run_id": str(run_id)}
        ]
        first = await stack.client.get("/api/conversations/page")
        assert first.status_code == 200 and len(first.json()["items"]) == 1
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-index@example.com"))
            assert user is not None
            tenant = Tenant(
                name="Other fixture company", slug="other-index", secrets_namespace="index-fixture"
            )
            db.add(tenant)
            await db.flush()
            other = Organization(tenant_id=tenant.id, name="Other fixture", slug="other-index")
            db.add(other)
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=tenant.id, user_id=user.id),
                    OrganizationMember(org_id=other.id, tenant_id=tenant.id, user_id=user.id),
                ]
            )
            await db.commit()
            other_id = other.id
            foreign_context = OrgContext(user_id=user.id, org_id=other.id, tenant_id=tenant.id)
            assert (
                await list_run_message_links(
                    db, RunMessageLinkQuery(conversation_id, foreign_context, [message_id])
                )
                == []
            )
            assert (
                await runs.get_active_run(
                    db, conversation_id=conversation_id, context=foreign_context
                )
                is None
            )
            assert (
                await runs.latest_run_for_conversation(
                    db, conversation_id=conversation_id, context=foreign_context
                )
                is None
            )
        stack.client.cookies.delete(ACTIVE_ORG_COOKIE)
        stack.client.cookies.set(ACTIVE_ORG_COOKIE, str(other_id))
        foreign = await stack.client.get("/api/conversations/page")
        assert foreign.status_code == 200 and foreign.json()["items"] == []
        foreign_agent = await stack.client.get(
            f"/api/agents/{agent.json()['id']}/conversations/page"
        )
        assert foreign_agent.status_code == 404, foreign_agent.text
        foreign_run = await stack.client.get(f"/api/conversations/{conversation_id}/runs/{run_id}")
        assert foreign_run.status_code == 404, foreign_run.text
