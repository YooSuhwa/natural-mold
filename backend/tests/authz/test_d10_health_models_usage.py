"""D-10 legacy gaps: health history target, model detail visibility, model test
credential, agent usage session/ownership (plan 9.5, 9.6)."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.service_types import Verdict
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser, get_current_user
from app.models.model import Model
from tests.authz.agent_route_fixture import seed_agent
from tests.authz.d04_support import OTHER_USER_ID, AuthzSwitch, allow, authz, seed_scope
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID

__all__ = ["authz"]  # fixture re-export


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


@pytest.fixture
def member(test_app: FastAPI) -> Iterator[None]:
    """The test user without the operator flag."""

    async def override() -> CurrentUser:
        return CurrentUser(id=TEST_USER_ID, email="t@t.io", name="T", is_super_user=False)

    previous = test_app.dependency_overrides[get_current_user]
    test_app.dependency_overrides[get_current_user] = override
    yield
    test_app.dependency_overrides[get_current_user] = previous


async def _model(db: AsyncSession, *, visible: bool = True) -> Model:
    model = Model(provider="openai", model_name=f"m-{uuid.uuid4().hex[:6]}", display_name="m")
    model.is_visible = visible
    db.add(model)
    await db.commit()
    return model


async def _credential(db: AsyncSession, owner: uuid.UUID = TEST_USER_ID):
    creation = CredentialCreation(definition_key="http_bearer", name="c", data={"token": "x"})
    credential = await create_test_credential(db, creation, unit_context(owner))
    await db.commit()
    return credential


async def test_legacy_health_history_of_an_unknown_model_is_404(client: AsyncClient) -> None:
    query = {"target_kind": "model", "target_id": str(uuid.uuid4())}
    response = await client.get("/api/health/history", params=query)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "MODEL_NOT_FOUND")


async def test_shadow_health_history_samples_the_located_model(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    model = await _model(db)
    authz.use(mode="shadow")
    query = {"target_kind": "model", "target_id": str(model.id)}
    assert (await client.get("/api/health/history", params=query)).json() == []
    assert [(s.obj, s.legacy) for s in authz.samples] == [(f"llm_model:{model.id}", Verdict.ALLOW)]


async def test_hidden_model_detail_is_404_for_members(
    client: AsyncClient, db, member: None, authz: AuthzSwitch
) -> None:
    hidden, shown = await _model(db, visible=False), await _model(db)
    response = await client.get(f"/api/models/{hidden.id}")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "MODEL_NOT_FOUND")
    assert (await client.get(f"/api/models/{shown.id}")).status_code == 200
    authz.use(allow(("can_use", f"llm_model:{hidden.id}")))  # a grant never shows hidden rows
    assert (await client.get(f"/api/models/{hidden.id}")).status_code == 404


async def test_hidden_model_detail_stays_visible_to_operators(client: AsyncClient, db) -> None:
    hidden = await _model(db, visible=False)
    assert (await client.get(f"/api/models/{hidden.id}")).status_code == 200


async def test_model_test_gates_the_explicit_credential(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    model, credential = await _model(db), await _credential(db)
    authz.use(allow(("can_use", f"llm_model:{model.id}")))
    probe = AsyncMock()
    with patch("app.routers.models.run_model_test", probe):
        response = await client.post(
            f"/api/models/{model.id}/test", params={"credential_id": str(credential.id)}
        )
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )
    probe.assert_not_awaited()


async def test_legacy_model_test_rejects_a_foreign_credential(client: AsyncClient, db) -> None:
    model, foreign = await _model(db), await _credential(db, OTHER_USER_ID)
    probe = AsyncMock()
    with patch("app.routers.models.run_model_test", probe):
        response = await client.post(
            f"/api/models/{model.id}/test", params={"credential_id": str(foreign.id)}
        )
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )
    probe.assert_not_awaited()


async def test_legacy_agent_usage_requires_a_session(raw_client: AsyncClient) -> None:
    agent_id = await seed_agent(owner=TEST_USER_ID)
    assert (await raw_client.get(f"/api/agents/{agent_id}/usage")).status_code == 401


async def test_legacy_agent_usage_of_a_foreign_agent_is_404(client: AsyncClient) -> None:
    mine, foreign = await seed_agent(owner=TEST_USER_ID), await seed_agent(owner=OTHER_USER_ID)
    assert (await client.get(f"/api/agents/{mine}/usage")).json()["total_tokens"] == 0
    response = await client.get(f"/api/agents/{foreign}/usage")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "AGENT_NOT_FOUND")


async def test_shadow_agent_usage_answers_the_owner_scope_and_samples(
    client: AsyncClient, authz: AuthzSwitch
) -> None:
    foreign = await seed_agent(owner=OTHER_USER_ID)
    authz.use(mode="shadow")
    assert (await client.get(f"/api/agents/{foreign}/usage")).status_code == 404
    assert [(s.obj, s.legacy) for s in authz.samples] == [(f"agent:{foreign}", Verdict.NOT_FOUND)]


async def test_hidden_model_health_and_test_are_404_for_members(
    client: AsyncClient, db, member: None
) -> None:
    hidden, credential = await _model(db, visible=False), await _credential(db)
    query = {"target_kind": "model", "target_id": str(hidden.id)}
    probe = AsyncMock()
    with patch("app.routers.models.run_model_test", probe):
        history = await client.get("/api/health/history", params=query)
        check = await client.post("/api/health/check", params=query)
        tested = await client.post(
            f"/api/models/{hidden.id}/test", params={"credential_id": str(credential.id)}
        )
    for response in (history, check, tested):
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "MODEL_NOT_FOUND",
        )
    probe.assert_not_awaited()


async def test_hidden_model_health_history_stays_visible_to_operators(
    client: AsyncClient, db
) -> None:
    hidden = await _model(db, visible=False)
    query = {"target_kind": "model", "target_id": str(hidden.id)}
    assert (await client.get("/api/health/history", params=query)).json() == []
