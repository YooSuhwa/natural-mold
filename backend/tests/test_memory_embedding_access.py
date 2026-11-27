"""A memory's provider must be available in its own company and organization."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, MODEL_ORGS_KEY, with_designation
from app.authz.platform_scope import PlatformScope
from app.config import settings
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.credential import Credential
from app.models.model import Model
from app.models.organization import Organization, OrganizationMember, Tenant
from app.services import memory_embedding_access as access
from app.services import memory_recall_policy, memory_vectors
from tests.authz.tenant_invitation_fixture import grant_tenant_admin
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, seed_agent
from tests.test_memory_recall_policy import _evaluated_report


async def selected(db: AsyncSession, *, company: bool) -> tuple[Tenant, Organization, Model]:
    await seed_agent(db)
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    org = await db.get(Organization, TEST_ORG_ID)
    assert tenant is not None and org is not None
    model = Model(
        tenant_id=tenant.id if company else None,
        provider="openai_compatible",
        model_name="semantic-search",
        display_name="Semantic search",
        kind="embedding",
        embedding_dimensions=8,
        base_url="https://embeddings.example/v1",
    )
    db.add(model)
    await db.flush()
    tenant.settings = {"memory": {"embedding_model_id": str(model.id), "embedding_generation": 1}}
    await db.commit()
    return tenant, org, model


def disabled(model_id: UUID) -> dict:
    return {"catalog_policy": {"items": [{"type": "model", "id": str(model_id), "enabled": False}]}}


@pytest.mark.asyncio
async def test_platform_model_follows_each_tier_and_current_selection(db: AsyncSession) -> None:
    tenant, org, model = await selected(db, company=False)
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=0) is None
    tenant.settings = {**tenant.settings, **disabled(model.id)}
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None
    tenant.settings = {"memory": {"embedding_model_id": str(uuid4()), "embedding_generation": 1}}
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is not None
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None

    tenant.limits = {"allow_system_models": False}
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None
    tenant.limits = {"allow_system_models": True}
    tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, [uuid4()])
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None
    tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, [org.id])
    org.settings = disabled(model.id)
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None
    org.settings = {}
    model.is_visible = False
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None


@pytest.mark.asyncio
async def test_company_model_uses_only_its_active_designated_organization(db: AsyncSession) -> None:
    tenant, org, model = await selected(db, company=True)
    other = Organization(tenant_id=tenant.id, name="Another", slug="another")
    db.add(other)
    await db.flush()
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None
    tenant.limits = {"allow_system_models": False}
    tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, [org.id])
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
    assert await access.org_embedder_for(db, tenant.id, other.id, model.id, generation=1) is None

    foreign = Tenant(name="Foreign", slug="foreign", secrets_namespace="tenants/foreign")
    db.add(foreign)
    await db.flush()
    foreign_org = Organization(tenant_id=foreign.id, name="Foreign", slug="foreign")
    db.add(foreign_org)
    await db.flush()
    assert await access.resolve_org_model(db, tenant.id, foreign_org.id, model.id) is None
    foreign.settings = with_designation({}, MODEL_ORGS_KEY, model.id, [foreign_org.id])
    assert await access.resolve_org_model(db, foreign.id, foreign_org.id, model.id) is None

    # Retain both ORM objects in the worker session while the database changes.
    await db.execute(
        update(Organization)
        .where(Organization.id == org.id)
        .values(status="suspended")
        .execution_options(synchronize_session=False)
    )
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    org.status = "active"
    tenant.status = "suspended"
    assert await access.resolve_org_model(db, tenant.id, org.id, model.id) is None


@pytest.mark.asyncio
async def test_credential_designation_and_status_are_checked_before_opening(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, org, model = await selected(db, company=False)
    credential = Credential(
        scope="tenant",
        tenant_id=tenant.id,
        definition_key=model.provider,
        name="Company endpoint",
        data_encrypted="fixture",
        key_id="fixture",
        is_system=True,
    )
    db.add(credential)
    await db.flush()
    model.default_credential_id = credential.id
    opening = AsyncMock(return_value=("fixture", model.base_url))
    monkeypatch.setattr(access, "open_model_key", opening)
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    opening.assert_not_awaited()
    tenant.settings = with_designation(
        tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, [org.id]
    )
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
    opening.assert_awaited_once()
    model.base_url = "http://local-model.example/v1"
    opening.return_value = (None, model.base_url)
    keyless = await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
    assert keyless is not None and keyless.api_key is None and keyless.base_url == model.base_url
    opening.side_effect = ValueError("invalid key material")
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    opening.reset_mock()
    credential.status = "disabled"
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    credential.status = "active"
    credential.definition_key = "openai"
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    opening.assert_not_awaited()


@pytest.mark.asyncio
async def test_company_model_can_only_use_a_permitted_platform_key_at_its_own_endpoint(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, org, model = await selected(db, company=True)
    tenant.settings = with_designation(tenant.settings, MODEL_ORGS_KEY, model.id, [org.id])
    credential = Credential(
        scope="platform",
        definition_key=model.provider,
        name="Platform endpoint",
        data_encrypted="fixture",
        key_id="fixture",
        is_system=True,
    )
    db.add(credential)
    await db.flush()
    opening = AsyncMock(return_value=("fixture", model.base_url))
    monkeypatch.setattr(access, "open_model_key", opening)
    embedder = await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
    assert embedder is not None and embedder.api_key == "fixture"
    opening.assert_awaited_once_with(
        db, credential_id=credential.id, provider=model.provider, tenant=tenant
    )
    model.base_url = "https://other.example/v1"
    keyless = await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
    assert keyless is not None and keyless.api_key is None and keyless.base_url == model.base_url
    model.default_credential_id = credential.id
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    tenant.limits = {"allow_system_models": False}
    opening.reset_mock()
    assert await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1) is None
    opening.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_key", [False, True], ids=["keyless", "credentialed"])
async def test_sibling_organizations_opt_in_to_the_same_model_independently(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound_key: bool
) -> None:
    tenant, first, model = await selected(db, company=False)
    second = Organization(tenant_id=tenant.id, name="Second", slug="second")
    db.add(second)
    await db.flush()
    db.add(OrganizationMember(tenant_id=tenant.id, org_id=second.id, user_id=TEST_USER_ID))
    if bound_key:
        credential = await credential_service.create(
            db,
            CredentialCreation(
                definition_key=model.provider,
                name="Registered gateway",
                data={"api_key": "fixture", "base_url": model.base_url},
            ),
            PlatformScope(),
        )
        model.default_credential_id = credential.id
    await grant_tenant_admin(db, tenant.id, TEST_USER_ID)
    report = _evaluated_report(model.id)
    report["candidate"].update(
        base_url=model.base_url, model=model.model_name, dimensions=model.embedding_dimensions
    )
    report_path = tmp_path / str(tenant.id) / f"{model.id}.json"
    report_path.parent.mkdir()
    report_path.write_text(json.dumps(report))
    monkeypatch.setattr(settings, "memory_recall_quality_dir", tmp_path)

    async def opt_in(org: Organization, mode: memory_recall_policy.RecallMode) -> None:
        await memory_vectors.change(
            db,
            tenant,
            replace(unit_context(TEST_USER_ID), org_id=org.id),
            model_id=model.id,
            confirm_count=0,
            recall_mode=mode,
        )

    async def bound_mode(org: Organization) -> memory_recall_policy.RecallMode:
        embedder = await access.org_embedder_for(db, tenant.id, org.id, model.id, generation=1)
        assert embedder is not None and embedder.registry_id == model.id
        assert embedder.api_key == ("fixture" if bound_key else None)
        return embedder.recall_mode

    await opt_in(first, "serve")
    assert (await bound_mode(first), await bound_mode(second)) == ("serve", "off")
    await opt_in(second, "shadow")
    assert (await bound_mode(first), await bound_mode(second)) == ("serve", "shadow")
    await opt_in(first, "off")
    assert (await bound_mode(first), await bound_mode(second)) == ("off", "shadow")
