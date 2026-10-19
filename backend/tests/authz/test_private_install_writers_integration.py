"""Actual m83 Agent publication/install writes private Blueprint and Installation roots."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem
from app.models.model import Model
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_agent_publication_and_installation_stamp_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, other_company: bool
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-install@example.com",
                "name": "Install fixture",
                "password": "native-install-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-install@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Install fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        published = await stack.client.post(
            f"/api/marketplace/items/from-agent/{agent.json()['id']}",
            headers=headers,
            json={"name": "Install fixture", "visibility": "private"},
        )
        assert published.status_code == 201, published.text
        installed = await stack.client.post(
            f"/api/marketplace/items/{published.json()['id']}/install",
            headers=headers,
            json={},
        )
        assert installed.status_code == 201, installed.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-install@example.com"))
            installation = await db.get(MarketplaceInstallation, UUID(installed.json()["id"]))
            assert user is not None and installation is not None
            blueprint = await db.get(AgentBlueprint, installation.installed_agent_blueprint_id)
            item = await db.get(MarketplaceItem, UUID(published.json()["id"]))
            assert blueprint is not None and item is not None
            for row in (item, installation, blueprint):
                assert row.user_id == user.id and row.org_id == user.last_active_org_id
                assert row.tenant_id == installation.tenant_id
