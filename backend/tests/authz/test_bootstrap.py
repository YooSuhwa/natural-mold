"""A-05 real SDK bootstrap, model pinning and transactional version records."""

import os
from uuid import uuid4

import pytest
from openfga_sdk.client import OpenFgaClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.authz.bootstrap import bootstrap
from app.authz.client import sdk_configuration
from app.authz.config import AuthzSettings
from app.authz.model_version import model_versions, version_metadata
from tests.authz.openfga_fixture import openfga_url as openfga_url

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
async def test_bootstrap_empty_store_then_reuses_exact_model(
    openfga_url: str, dialect: str
) -> None:
    # Given: a unique OpenFGA store and empty source version table.
    settings = AuthzSettings(
        openfga_api_url=openfga_url,
        openfga_api_token=SecretStr("p0-sdk-local-dummy"),
        openfga_store_name="bootstrap-" + uuid4().hex,
        openfga_timeout_ms=5000,
    )
    schema = "authz_bootstrap_" + uuid4().hex
    engine = create_async_engine(
        os.environ["INTEGRATION_DATABASE_URL"]
        if dialect == "postgresql"
        else "sqlite+aiosqlite://",
        execution_options={"schema_translate_map": {None: schema}}
        if dialect == "postgresql"
        else {},
    )
    try:
        async with engine.begin() as conn:
            if dialect == "postgresql":
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(version_metadata.create_all)
        async with OpenFgaClient(sdk_configuration(settings)) as sdk:
            try:
                async with AsyncSession(engine) as db:
                    # When: a first bootstrap deploys the canonical model.
                    first = await bootstrap(db, sdk, settings)
                    await db.commit()
                    settings.openfga_store_id = first.store_id
                    assert first.changed
                    # Then: the model ID is durable, and a repeat creates no new version.
                    second = await bootstrap(db, sdk, settings)
                    await db.commit()
                    assert second.model_id == first.model_id
                    assert not second.changed
                    rows = (await db.execute(select(model_versions))).all()
                    assert len(rows) == 1
                    assert rows[0]._mapping["is_active"] is True
                    remote = await sdk.read_authorization_models()
                    assert len(remote.authorization_models) == 1
            finally:
                if settings.openfga_store_id:
                    sdk.set_store_id(settings.openfga_store_id)
                    await sdk.delete_store()
    finally:
        if dialect == "postgresql":
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
