"""Real PostgreSQL source rows and the canonical model produce permission decisions."""

import os

import pytest
from openfga_sdk.client import OpenFgaClient
from openfga_sdk.client.models.tuple import ClientTuple
from openfga_sdk.client.models.write_request import ClientWriteRequest
from openfga_sdk.models.create_store_request import CreateStoreRequest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.authz.client import CheckQuery, open_client, sdk_configuration
from app.authz.config import AuthzSettings
from app.authz.model_document import sdk_model
from tests.authz.openfga_fixture import openfga_url as openfga_url

pytestmark = pytest.mark.integration


async def test_postgres_relationships_allow_only_members_when_projected_to_fga(openfga_url: str):
    # Given: source relationships live in a disposable Postgres transaction.
    engine = create_async_engine(os.environ["INTEGRATION_DATABASE_URL"])
    config = AuthzSettings(
        openfga_api_url=openfga_url,
        openfga_api_token=SecretStr("p0-sdk-local-dummy"),
        openfga_timeout_ms=5000,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    'CREATE TEMP TABLE authz_proto ("user" text, relation text, object text) '
                    "ON COMMIT DROP"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO authz_proto VALUES "
                    "('tenant:t','tenant','organization:o'), "
                    "('user:owner','member','tenant:t'), "
                    "('user:owner','member','organization:o'), "
                    "('organization:o','org','agent:a'), "
                    "('user:owner','owner','agent:a'), "
                    "('user:outsider','consumer','agent:a')"
                )
            )
            rows = (
                await connection.execute(text('SELECT "user", relation, object FROM authz_proto'))
            ).all()
            tuples = [CheckQuery.model_validate(row._mapping) for row in rows]
            async with OpenFgaClient(sdk_configuration(config)) as admin:
                store = await admin.create_store(CreateStoreRequest(name="p0-pg-source"))
                admin.set_store_id(store.id)
                config.openfga_store_id = store.id
                try:
                    deployed = await admin.write_authorization_model(sdk_model())
                    config.openfga_model_id = deployed.authorization_model_id
                    admin.set_authorization_model_id(config.openfga_model_id)
                    await admin.write(
                        ClientWriteRequest(
                            writes=[
                                ClientTuple(user=row.user, relation=row.relation, object=row.object)
                                for row in tuples
                            ]
                        )
                    )
                    # When: the typed production adapter evaluates both subjects.
                    async with open_client(config) as fga:
                        results = await fga.batch_check(
                            (
                                CheckQuery(user="user:owner", relation="can_run", object="agent:a"),
                                CheckQuery(
                                    user="user:outsider", relation="can_run", object="agent:a"
                                ),
                            )
                        )
                    # Then: even an incorrectly written consumer tuple cannot bypass membership.
                    assert results == (True, False)
                finally:
                    await admin.delete_store()
    finally:
        await engine.dispose()
