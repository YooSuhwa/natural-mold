"""Independent exact-SHA HTTP Installation update parent witness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace.install_service import update_installation
from app.marketplace.schemas import UpdateMarketplaceInstallationIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.models.tool import Tool
from tests.authz.blueprint_preflight_fixture import materialization_sql

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_scope import AgentCase

pytest_plugins = ["tests.authz.test_private_agent_scope"]


@dataclass(frozen=True, slots=True)
class Witness:
    actor: AgentCase
    scopes: PrivateScopes


@pytest.fixture
def witness(agent_case: AgentCase, private_scopes: PrivateScopes) -> Witness:
    return Witness(agent_case, private_scopes)


@pytest.mark.parametrize(
    "case",
    [
        ("overwrite", False),
        ("overwrite", True),
        ("keep_current", False),
        ("keep_current", True),
        ("install_new_copy", False),
        ("install_new_copy", True),
    ],
)
async def test_current_version_parent_on_actual_update(
    db: AsyncSession,
    witness: Witness,
    case: tuple[Literal["overwrite", "keep_current", "install_new_copy"], bool],
) -> None:
    strategy, foreign = case
    actor, scopes = witness.actor, witness.scopes
    csrf = actor.client.cookies.get("moldy_csrf")
    assert csrf is not None
    headers = {"X-CSRF-Token": csrf}
    publication = await actor.client.post(
        f"/api/marketplace/items/from-agent/{actor.agent_id}",
        json={"name": "Original fixture source", "visibility": "private"},
        headers=headers,
    )
    assert publication.status_code == 201, publication.text
    item_id = UUID(publication.json()["id"])
    installation = await actor.client.post(
        f"/api/marketplace/items/{item_id}/install", json={}, headers=headers
    )
    assert installation.status_code == 201, installation.text
    installation_id = installation.json()["id"]
    blueprint_id = UUID(installation.json()["installed_agent_blueprint_id"])
    source = await db.get(MarketplaceItem, item_id)
    assert source is not None and source.latest_version_id is not None
    version_id = source.latest_version_id
    version = await db.get(MarketplaceVersion, version_id)
    assert version is not None
    if foreign:
        other = scopes.second
        target = MarketplaceItem(
            resource_type="agent",
            owner_user_id=other.user_id,
            org_id=other.org_id,
            tenant_id=other.tenant_id,
            is_system=False,
            name="Other fixture source",
            slug="other-fixture-source",
            visibility="private",
            status="published",
        )
        db.add(target)
        await db.flush()
        await db.execute(
            update(MarketplaceVersion)
            .where(MarketplaceVersion.id == version_id)
            .values(
                item_id=target.id,
                payload={
                    "agent": {
                        "name": "Changed fixture",
                        "system_prompt": "ASSOCIATION_FIXTURE_TEXT",
                    },
                    "capabilities": {},
                    "setup": {"required_credentials": []},
                },
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        persisted_parent = await db.scalar(
            select(MarketplaceVersion.item_id).where(MarketplaceVersion.id == version_id)
        )
        assert persisted_parent == target.id and version.item_id == item_id
    with materialization_sql(db) as dml:
        response = await actor.client.post(
            f"/api/marketplace/installations/{installation_id}/update",
            json={"strategy": strategy},
            headers=headers,
        )
    await db.refresh(version)
    root = await db.get(AgentBlueprint, blueprint_id)
    assert root is not None
    await db.refresh(root)
    marker = "ASSOCIATION_FIXTURE_TEXT" in str(root.spec)
    if foreign:
        assert response.status_code == 404, (response.status_code, response.text, marker, dml)
        assert not marker and dml == []
    else:
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("case", ["normal", "moved_source", "missing_source", "moved_version"])
async def test_update_preflight_preserves_unrelated_pending_write(
    db: AsyncSession, witness: Witness, case: str
) -> None:
    actor, scopes = witness.actor, witness.scopes
    own = scopes.first
    csrf = actor.client.cookies.get("moldy_csrf")
    assert csrf is not None
    headers = {"X-CSRF-Token": csrf}
    publication = await actor.client.post(
        f"/api/marketplace/items/from-agent/{actor.agent_id}",
        json={"name": "Pending update fixture", "visibility": "private"},
        headers=headers,
    )
    assert publication.status_code == 201, publication.text
    item_id = UUID(publication.json()["id"])
    installed = await actor.client.post(
        f"/api/marketplace/items/{item_id}/install", json={}, headers=headers
    )
    assert installed.status_code == 201, installed.text
    source = await db.get(MarketplaceItem, item_id)
    assert source is not None and source.latest_version_id is not None
    if case == "moved_source":
        await db.execute(
            update(MarketplaceItem)
            .where(MarketplaceItem.id == item_id)
            .values(org_id=scopes.second.org_id, tenant_id=scopes.second.tenant_id)
            .execution_options(synchronize_session=False)
        )
    elif case == "missing_source":
        from app.models.marketplace import MarketplaceInstallation

        await db.execute(
            update(MarketplaceInstallation)
            .where(MarketplaceInstallation.id == UUID(installed.json()["id"]))
            .values(item_id=uuid4())
            .execution_options(synchronize_session=False)
        )
    elif case == "moved_version":
        await db.execute(
            update(MarketplaceVersion)
            .where(MarketplaceVersion.id == source.latest_version_id)
            .values(item_id=uuid4())
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    pending = Tool(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Unrelated pending update fixture",
        definition_key="http_request",
    )
    db.add(pending)
    denied = False
    with materialization_sql(db) as dml:
        try:
            result = await update_installation(
                db,
                installation_id=UUID(installed.json()["id"]),
                user=CurrentUser(id=own.user_id, email="fixture@example.test", name="Fixture"),
                context=own,
                body=UpdateMarketplaceInstallationIn(strategy="keep_current"),
            )
            assert result.id == UUID(installed.json()["id"])
        except AppError as error:
            assert error.status == 404
            denied = True
    assert denied is (case != "normal")
    assert dml == [] and pending in db.new
    if case == "normal":
        await db.flush()
        assert pending not in db.new
    await db.rollback()
