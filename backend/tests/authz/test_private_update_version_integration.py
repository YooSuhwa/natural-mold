"""Actual m83 install updates retain the source/version association."""

from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.models.model import Model
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "case", [("overwrite", False), ("overwrite", True), ("keep_current", True)]
)
async def test_native_update_requires_version_parent(
    monkeypatch: pytest.MonkeyPatch, case: tuple[Literal["overwrite", "keep_current"], bool]
) -> None:
    strategy, moved = case
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "update-fixture@example.com",
                "name": "Update fixture",
                "password": "native-update-fixture-password",
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
            json={"name": "Update fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert agent.status_code == 201, agent.text
        publication = await stack.client.post(
            f"/api/marketplace/items/from-agent/{agent.json()['id']}",
            headers=headers,
            json={"name": "Update fixture", "visibility": "private"},
        )
        assert publication.status_code == 201, publication.text
        item_id = UUID(publication.json()["id"])
        installed = await stack.client.post(
            f"/api/marketplace/items/{item_id}/install",
            headers=headers,
            json={},
        )
        assert installed.status_code == 201, installed.text
        blueprint_id = UUID(installed.json()["installed_agent_blueprint_id"])
        async with stack.sessions() as db:
            source = await db.get(MarketplaceItem, item_id)
            original = await db.get(AgentBlueprint, blueprint_id)
            assert source is not None and original is not None
            original_spec = dict(original.spec)
            before = await db.scalar(select(func.count()).select_from(AgentBlueprint))
            if moved:
                target = MarketplaceItem(
                    owner_user_id=source.owner_user_id,
                    org_id=source.org_id,
                    tenant_id=source.tenant_id,
                    resource_type="agent",
                    name="Other native fixture",
                    slug=f"fixture-{uuid4().hex}",
                    visibility="private",
                    status="published",
                    is_system=False,
                )
                db.add(target)
                await db.flush()
                await db.execute(
                    update(MarketplaceVersion)
                    .where(MarketplaceVersion.id == source.latest_version_id)
                    .values(
                        item_id=target.id, payload={"agent": {"system_prompt": "Changed fixture"}}
                    )
                )
                await db.commit()
        response = await stack.client.post(
            f"/api/marketplace/installations/{installed.json()['id']}/update",
            headers=headers,
            json={"strategy": strategy},
        )
        assert response.status_code == (404 if moved else 200), response.text
        async with stack.sessions() as db:
            current = await db.get(AgentBlueprint, blueprint_id)
            assert current is not None and current.spec == original_spec
            assert await db.scalar(select(func.count()).select_from(AgentBlueprint)) == before
        detail = await stack.client.get(f"/api/marketplace/items/{item_id}")
        assert detail.status_code == 200, detail.text
        assert (detail.json()["latest_version"] is None) is moved
