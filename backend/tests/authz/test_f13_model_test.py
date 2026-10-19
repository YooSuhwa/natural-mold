"""F-13: a model test run needs model ``can_use`` + credential ``can_use`` (plan 11.0)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.creation import CredentialCreation
from app.models.model import Model
from app.services.model_test import ModelTestResult
from tests.authz.d04_support import AuthzSwitch, allow, authz, seed_scope
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID

__all__ = ["authz"]  # fixture re-export

PROBE = "app.services.model_test_run.run_model_test"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _model_with_default(db: AsyncSession) -> tuple[Model, str, str]:
    creation = CredentialCreation(definition_key="openai", name="c", data={"api_key": "x"})
    credential = await create_test_credential(db, creation, unit_context(TEST_USER_ID))
    model = Model(provider="openai", model_name="f13-model", display_name="m")
    model.default_credential_id = credential.id
    db.add(model)
    await db.commit()
    return model, f"llm_model:{model.id}", f"credential:{credential.id}"


def _probe() -> AsyncMock:
    return AsyncMock(return_value=ModelTestResult(success=True, latency_ms=1))


async def test_model_test_refuses_after_the_default_credential_is_revoked(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    model, model_obj, credential_obj = await _model_with_default(db)
    probe = _probe()
    authz.use(allow(("can_use", model_obj), ("can_use", credential_obj)))
    with patch(PROBE, probe):
        assert (await client.post(f"/api/models/{model.id}/test")).status_code == 200
    probe.assert_awaited_once()

    authz.use(allow(("can_use", model_obj)))  # credential can_use revoked
    with patch(PROBE, probe):
        response = await client.post(f"/api/models/{model.id}/test")
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )
    probe.assert_awaited_once()  # not sent again


async def test_model_test_refuses_after_the_model_is_revoked(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    model, _, credential_obj = await _model_with_default(db)
    probe = _probe()
    authz.use(allow(("can_use", credential_obj)))  # model can_use revoked
    with patch(PROBE, probe):
        response = await client.post(f"/api/models/{model.id}/test")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "MODEL_NOT_FOUND")
    probe.assert_not_awaited()


async def test_legacy_default_credential_keeps_the_owner_answer(
    client: AsyncClient, db: AsyncSession
) -> None:
    model, _, _ = await _model_with_default(db)
    probe = _probe()
    with patch(PROBE, probe):
        assert (await client.post(f"/api/models/{model.id}/test")).status_code == 200
    probe.assert_awaited_once()
