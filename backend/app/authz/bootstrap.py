"""Deploy an immutable canonical model and transactionally pin its version."""

from openfga_sdk.client import OpenFgaClient
from openfga_sdk.models.create_store_request import CreateStoreRequest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.config import AuthzSettings
from app.authz.model_document import model_sha256, sdk_model
from app.authz.model_version import ModelVersion, model_versions


class BootstrapResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    store_id: str
    model_id: str
    model_sha256: str
    changed: bool


async def bootstrap(
    db: AsyncSession, sdk: OpenFgaClient, settings: AuthzSettings
) -> BootstrapResult:
    """Caller commits; require m84's table and never perform implicit schema DDL.

    A PostgreSQL transaction lock serializes deployment on the installation DB.
    Remote model writes are immutable; a failed commit may leave an unused model,
    but can never activate an unrecorded model in the application.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(2468300105)"))
    row = (
        (await db.execute(select(model_versions).where(model_versions.c.is_active.is_(True))))
        .mappings()
        .first()
    )
    active = ModelVersion.model_validate(row) if row is not None else None
    store_id = settings.openfga_store_id or (active.store_id if active else "")
    if not store_id:
        stores = await sdk.list_stores({"name": settings.openfga_store_name, "page_size": 2})
        if len(stores.stores) > 1 or stores.continuation_token:
            raise ValueError("Multiple matching stores; configure OPENFGA_STORE_ID explicitly")
        store_id = (
            stores.stores[0].id
            if stores.stores
            else (await sdk.create_store(CreateStoreRequest(name=settings.openfga_store_name))).id
        )
    sdk.set_store_id(store_id)
    await sdk.get_store()
    digest = model_sha256()
    if active and active.store_id == store_id and active.model_sha256 == digest:
        sdk.set_authorization_model_id(active.model_id)
        await sdk.read_authorization_model()
        return BootstrapResult(
            store_id=store_id, model_id=active.model_id, model_sha256=digest, changed=False
        )
    deployed = await sdk.write_authorization_model(sdk_model())
    model_id = deployed.authorization_model_id
    sdk.set_authorization_model_id(model_id)
    await db.execute(
        update(model_versions).where(model_versions.c.is_active.is_(True)).values(is_active=False)
    )
    await db.execute(
        insert(model_versions).values(
            store_id=store_id, model_id=model_id, model_sha256=digest, is_active=True
        )
    )
    return BootstrapResult(store_id=store_id, model_id=model_id, model_sha256=digest, changed=True)
