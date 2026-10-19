"""E-07 review: a company A model is never reachable from company B (or platform paths)."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.read_tools import build_read_tools
from app.authz.model_access import load_model_target
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.exceptions import AppError
from app.models.organization import Tenant
from app.schemas.admin_tenant import TenantLimitSummary
from app.schemas.agent import AgentUpdate
from app.services.agent_binding_service import _validate_agent_binding_ids
from app.services.agent_blueprint_service import (
    _resolve_model_fallback_ids,
    _resolve_model_id,
)
from app.services.builder_session_service import _get_default_model_name
from tests.authz.d04_support import OwnerStore, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e07_support import company_model, foreign_tenant, platform_model
from tests.authz.e09_support import MEMBER, acting_as
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

CTX = OrgContext(tenant_id=TEST_TENANT_ID, org_id=TEST_ORG_ID, user_id=TEST_USER_ID)
DESCRIPTOR = {"provider": "openai", "model_name": "foreign"}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _foreign(db: AsyncSession):
    return await company_model(db, await foreign_tenant(db), "Foreign")


async def test_blueprint_resolution_is_company_scoped(db: AsyncSession) -> None:
    foreign = await _foreign(db)
    own = await company_model(db, TEST_TENANT_ID, "Own")
    with pytest.raises(AppError):
        await _resolve_model_id(db, spec={}, requested_model_id=foreign.id, tenant_id=CTX.tenant_id)
    preferred = {"agent": {"model": {"preferred_model_id": str(foreign.id), **DESCRIPTOR}}}
    with pytest.raises(AppError):
        await _resolve_model_id(
            db, spec=preferred, requested_model_id=None, tenant_id=CTX.tenant_id
        )
    fallbacks = {"agent": {"model_fallbacks": [DESCRIPTOR]}}
    with pytest.raises(AppError):
        await _resolve_model_fallback_ids(
            db, spec=fallbacks, requested_model_fallback_ids=None, tenant_id=CTX.tenant_id
        )
    resolved = await _resolve_model_id(
        db, spec={}, requested_model_id=own.id, tenant_id=CTX.tenant_id
    )
    assert resolved == own.id


async def test_builder_default_model_is_platform_only(db: AsyncSession) -> None:
    await _foreign(db)  # oldest row, a company model
    with patch("app.config.settings.default_agent_model", ""):
        assert await _get_default_model_name(db) == ""
        await platform_model(db)
        assert await _get_default_model_name(db) == "openai:platform"


async def test_assistant_lists_platform_and_own_company_models(db: AsyncSession) -> None:
    foreign = await _foreign(db)
    own = await company_model(db, TEST_TENANT_ID, "Own")
    hidden = await platform_model(db, "Hidden", is_visible=False)
    platform = await platform_model(db)
    with patch("app.agent_runtime.assistant.tools.read_tools.async_session_factory", TestSession):
        tools = build_read_tools(db, uuid4(), CTX)
        tool = next(t for t in tools if t.name == "list_available_models")
        listed = {row["id"] for row in json.loads(await tool.ainvoke({}))}
    assert listed == {str(own.id), str(platform.id)}
    assert str(foreign.id) not in listed and str(hidden.id) not in listed


async def test_health_is_company_scoped(client: AsyncClient, db: AsyncSession) -> None:
    foreign = await _foreign(db)
    platform = await platform_model(db)
    ids = {row["target_id"] for row in (await client.get("/api/health/models")).json()}
    assert str(platform.id) in ids and str(foreign.id) not in ids
    legacy = AuthzService(OwnerStore(), mode="legacy")
    with pytest.raises(AppError) as error:
        await load_model_target(db, legacy, CTX, foreign.id, MEMBER)
    assert error.value.status == 404


async def test_agent_bindings_refuse_another_companys_models(db: AsyncSession) -> None:
    foreign = await _foreign(db)
    own = await company_model(db, TEST_TENANT_ID, "Own")
    for data in (AgentUpdate(model_id=foreign.id), AgentUpdate(model_fallback_ids=[foreign.id])):
        with pytest.raises(HTTPException) as error:
            await _validate_agent_binding_ids(db, data, CTX)
        assert error.value.status_code == 400
    await _validate_agent_binding_ids(
        db, AgentUpdate(model_id=own.id, model_fallback_ids=[own.id]), CTX
    )


async def test_absent_limit_reads_as_allowed(db: AsyncSession) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    assert TenantLimitSummary.from_tenant(tenant).allow_system_models is True
    tenant.limits = {"allow_system_models": False}
    assert TenantLimitSummary.from_tenant(tenant).allow_system_models is False
