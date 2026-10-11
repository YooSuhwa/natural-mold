"""Actual m83 catalog publishing and default credentials preserve private scope."""

from uuid import UUID

import pytest
from sqlalchemy import select

from app.authz.agent_scope import resolve_agent_actor
from app.config import settings
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.credential_default import CredentialDefault
from app.models.marketplace import MarketplaceItem
from app.models.model import Model
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_catalog_publish_and_default_mapping_use_real_m83_columns(
    monkeypatch: pytest.MonkeyPatch,
    other_company: bool,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-catalog@example.com",
                "name": "Catalog fixture",
                "password": "native-catalog-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-catalog@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Catalog fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        published = await stack.client.post(
            f"/api/marketplace/items/from-agent/{agent.json()['id']}",
            headers=headers,
            json={"name": "Catalog fixture", "visibility": "private"},
        )
        assert published.status_code == 201, published.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-catalog@example.com"))
            item = await db.get(MarketplaceItem, UUID(published.json()["id"]))
            assert user is not None and item is not None
            context = await resolve_agent_actor(db, UUID(agent.json()["id"]), user.id)
            assert (item.user_id, item.org_id, item.tenant_id) == (
                context.user_id,
                context.org_id,
                context.tenant_id,
            )
            assert item.tenant_publication == "none"
            assert "user_id" not in MarketplaceItem.__table__.columns
            credential = await credential_service.create(
                db,
                CredentialCreation("openai", "Native default", {"api_key": "dummy-default"}),
                context,
            )
            default = CredentialDefault(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                scope_kind="llm_model",
                scope_key="fixture",
                credential_id=credential.id,
            )
            db.add(default)
            await db.commit()
            await db.refresh(default)
            assert default.org_id == context.org_id and default.tenant_id == context.tenant_id
