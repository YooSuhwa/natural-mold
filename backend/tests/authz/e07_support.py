"""E-07 company settings tier seeds (plan 7.2, 8.9, 9.7, S07)."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.credentials import tenant_encryption
from app.models.credential import Credential
from app.models.model import Model
from app.models.organization import Tenant
from app.models.system_llm_setting import SystemLlmSetting
from app.models.tool import Tool
from tests.authz.d04_support import OwnerStore
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession

FOREIGN_TENANT_ID = uuid5(NAMESPACE_URL, "moldy/e07-foreign-tenant")
TENANT = f"/api/tenants/{TEST_TENANT_ID}"


async def foreign_tenant(db: AsyncSession) -> UUID:
    if await db.get(Tenant, FOREIGN_TENANT_ID) is None:
        db.add(
            Tenant(
                id=FOREIGN_TENANT_ID,
                name="Foreign",
                slug="e07-foreign",
                secrets_namespace="e07-foreign",
            )
        )
        await db.commit()
    return FOREIGN_TENANT_ID


async def platform_model(db: AsyncSession, name: str = "Platform", **fields: object) -> Model:
    model = Model(provider="openai", model_name=name.lower(), display_name=name, **fields)
    db.add(model)
    await db.commit()
    return model


async def company_model(db: AsyncSession, tenant_id: UUID, name: str = "Company") -> Model:
    model = Model(provider="openai", model_name=name.lower(), display_name=name)
    model.tenant_id = tenant_id
    db.add(model)
    await db.commit()
    return model


async def system_tool(db: AsyncSession) -> Tool:
    tool = Tool(name="Web", definition_key="builtin:current_datetime", is_system=True)
    db.add(tool)
    await db.commit()
    return tool


async def credential(
    db: AsyncSession, *, tenant_id: UUID | None, key: str = "sk-platform"
) -> Credential:
    """A platform (``tenant_id=None``) or company shared openai credential."""
    row = Credential(
        id=uuid4(),
        user_id=None,
        org_id=None,
        tenant_id=tenant_id,
        scope="platform" if tenant_id is None else "tenant",
        definition_key="openai",
        name=f"key-{key}",
        is_system=True,
        status="active",
    )
    await tenant_encryption.seal(row, {"api_key": key}, db=db)
    db.add(row)
    await db.commit()
    return row


async def platform_llm(db: AsyncSession, credential_id: UUID, role: str = "text_primary") -> None:
    db.add(SystemLlmSetting(role=role, credential_id=credential_id, model_name="gpt-platform"))
    await db.commit()


async def set_limit(db: AsyncSession, *, allow_system_models: bool) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.limits = {**(tenant.limits or {}), "allow_system_models": allow_system_models}
    await db.commit()


@contextmanager
def enforced(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[OwnerStore]:
    """Serve the routes in enforce mode against a fake tuple store."""
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    store = OwnerStore()
    service = AuthzService(store, mode="enforce")

    async def override() -> AuthzService:
        return service

    app.dependency_overrides[get_authz] = override
    try:
        yield store
    finally:
        app.dependency_overrides.pop(get_authz, None)


def stored(store: OwnerStore) -> set[tuple[str, str, str]]:
    return {(t.user, t.relation, t.object) for t in store.stored()}
