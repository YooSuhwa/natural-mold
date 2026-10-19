"""D-04 credential, model and health routes: shadow samples and enforce decisions (9.5)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.service_types import Verdict
from app.credentials.creation import CredentialCreation
from app.models.model import Model
from tests.authz.d04_support import (
    OTHER_USER_ID,
    AuthzSwitch,
    allow,
    authz,
    grant_to_me,
    seed_scope,
)
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID

__all__ = ["authz"]  # fixture re-export


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _credential(db: AsyncSession, owner=TEST_USER_ID):
    creation = CredentialCreation(definition_key="http_bearer", name="c", data={"token": "x"})
    credential = await create_test_credential(db, creation, unit_context(owner))
    await db.commit()
    return credential


async def _model(db: AsyncSession, name: str = "m") -> Model:
    model = Model(provider="openai", model_name=name, display_name=name, is_visible=True)
    db.add(model)
    await db.commit()
    return model


async def test_shadow_credential_detail_samples_and_keeps_legacy_404(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    other = await _credential(db, OTHER_USER_ID)
    authz.use(mode="shadow")
    response = await client.get(f"/api/credentials/{other.id}")
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )
    assert [(s.obj, s.legacy) for s in authz.samples] == [
        (f"credential:{other.id}", Verdict.NOT_FOUND)
    ]


async def test_enforce_credential_grantee_reads_metadata_but_cannot_edit(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    other = await _credential(db, OTHER_USER_ID)
    await grant_to_me(db, "credential", other.id)
    authz.use(allow(("can_use", f"credential:{other.id}")))
    assert (await client.get(f"/api/credentials/{other.id}")).status_code == 200
    assert [row["id"] for row in (await client.get("/api/credentials")).json()] == [str(other.id)]
    edit = await client.patch(f"/api/credentials/{other.id}", json={"name": "n"})
    assert edit.status_code == 403
    assert edit.json()["error"]["details"]["required"] == "can_edit"


async def test_enforce_credential_without_tuple_is_the_existing_404(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    mine = await _credential(db)
    authz.use()
    response = await client.get(f"/api/credentials/{mine.id}")
    assert response.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"
    assert (await client.get("/api/credentials")).json() == []


async def test_enforce_model_catalog_returns_only_allowed_models(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    allowed, hidden = await _model(db, "allowed"), await _model(db, "hidden")
    authz.use(allow(("can_use", f"llm_model:{allowed.id}")))
    assert [row["id"] for row in (await client.get("/api/models")).json()] == [str(allowed.id)]
    assert (await client.get(f"/api/models/{allowed.id}")).status_code == 200
    missing = await client.get(f"/api/models/{hidden.id}")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "MODEL_NOT_FOUND")
    health = await client.get("/api/health/models")
    assert [row["target_id"] for row in health.json()] == [str(allowed.id)]


async def test_legacy_model_routes_resolve_no_organization(client: AsyncClient, db) -> None:
    model = await _model(db)
    assert (await client.get(f"/api/models/{model.id}")).status_code == 200
    ids = {row["id"] for row in (await client.get("/api/models")).json()}
    assert str(model.id) in ids


async def test_health_history_checks_the_target_when_enforced(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    model = await _model(db)
    query = {"target_kind": "model", "target_id": str(model.id)}
    assert (await client.get("/api/health/history", params=query)).json() == []
    authz.use()
    denied = await client.get("/api/health/history", params=query)
    assert (denied.status_code, denied.json()["error"]["code"]) == (404, "MODEL_NOT_FOUND")
    authz.use(mode="shadow")
    assert (await client.get("/api/health/history", params=query)).status_code == 200
    assert authz.samples[-1].legacy is Verdict.ALLOW
