"""The frozen child loses its send permit on a changed model, credential, tool or policy."""

from __future__ import annotations

import re

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.independent_job_capabilities import independent_capability_fingerprint
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.mcp_tool import AgentMcpToolLink
from app.models.model import Model
from app.models.organization import Organization
from app.models.resource_policy import ResourcePolicy
from app.models.tool import AgentToolLink
from tests.authz.d04_support import add_server, add_tool
from tests.authz.e01_support import add_agent, add_credential, seed
from tests.authz.f05_support import CALLER, OWNER, bind, enforce, per_user_tool
from tests.authz.test_company_credential_tools import _company, _designate
from tests.authz.unit_scope_fixture import unit_context

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def child(db: AsyncSession) -> Agent:
    await seed(db)
    return await add_agent(db, owner=CALLER)


async def test_ciphertext_rotation_and_auto_match_are_pinned_without_opening(
    db: AsyncSession, child: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_decryption(*args: object, **kwargs: object) -> None:
        raise AssertionError("authorization opened a credential")

    monkeypatch.setattr("app.credentials.service.decrypt_with_external", no_decryption)
    ctx = unit_context(CALLER)
    baseline = await independent_capability_fingerprint(db, child, ctx)
    assert re.fullmatch("[a-f0-9]{64}", baseline)
    credential = await add_credential(db, definition_key="openai")
    auto = await independent_capability_fingerprint(db, child, ctx)
    assert auto != baseline
    credential.data_encrypted = "fixture-rotated-encrypted-blob"
    await db.commit()
    rotated = await independent_capability_fingerprint(db, child, ctx)
    assert rotated != auto
    credential.allowed_hosts = ["mock.invalid"]
    await db.commit()
    assert await independent_capability_fingerprint(db, child, ctx) != rotated
    credential.status = "revoked"
    await db.commit()
    assert await independent_capability_fingerprint(db, child, ctx) == baseline


async def test_changed_resource_and_company_policy_break_pin(
    db: AsyncSession, child: Agent
) -> None:
    ctx = unit_context(CALLER)
    tool = await add_tool(db, owner=CALLER)
    link = AgentToolLink(agent_id=child.id, tool_id=tool.id)
    db.add(link)
    await db.commit()
    original = await independent_capability_fingerprint(db, child, ctx)
    assert await independent_capability_fingerprint(db, child, ctx) == original
    tool.parameters = {"method": "GET", "url": "https://mock.invalid/search"}
    await db.commit()
    changed = await independent_capability_fingerprint(db, child, ctx)
    assert changed != original
    db.add(ResourcePolicy(tenant_id=ctx.tenant_id, resource_key=f"tool:{tool.id}", enabled=False))
    await db.commit()
    assert await independent_capability_fingerprint(db, child, ctx) != changed
    link.link_status = "broken"
    await db.commit()
    with pytest.raises(AppError, match="이 작업에서"):
        await independent_capability_fingerprint(db, child, ctx)


async def test_removed_per_user_tool_binding_fails_closed(db: AsyncSession, child: Agent) -> None:
    ctx = unit_context(CALLER)
    tool = await per_user_tool(db, child, owner=CALLER)
    with pytest.raises(AppError) as missing:
        await independent_capability_fingerprint(db, child, ctx)
    assert missing.value.code == "credential_binding_required"
    credential = await add_credential(db)
    await bind(db, CALLER, "tool", tool.id, credential)
    before = await independent_capability_fingerprint(db, child, ctx)
    credential.status = "revoked"
    await db.commit()
    with pytest.raises(AppError) as revoked:
        await independent_capability_fingerprint(db, child, ctx)
    assert revoked.value.code == "credential_binding_required"
    assert before


async def test_hidden_and_org_denied_models_are_not_eligible_at_acceptance(
    db: AsyncSession, child: Agent
) -> None:
    ctx = unit_context(CALLER)
    model = await db.get(Model, child.model_id)
    assert model
    model.is_visible = False
    await db.commit()
    with pytest.raises(AppError):
        await independent_capability_fingerprint(db, child, ctx)
    model.is_visible = True
    org = await db.get(Organization, ctx.org_id)
    assert org
    org.settings = {
        "catalog_policy": {"items": [{"type": "model", "id": str(model.id), "enabled": False}]}
    }
    await db.commit()
    with pytest.raises(AppError):
        await independent_capability_fingerprint(db, child, ctx)


async def test_fresh_grant_decisions_exclude_an_optional_tool_after_revocation(
    db: AsyncSession, child: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = unit_context(CALLER)
    child.identity_mode = "per_user"
    tool = await add_tool(db, owner=OWNER)
    model_permission = (f"user:{CALLER}", "can_use", f"llm_model:{child.model_id}")
    db.add(AgentToolLink(agent_id=child.id, tool_id=tool.id))
    await db.commit()
    monkeypatch.setattr(
        runtime,
        "_current",
        enforce(model_permission, (f"user:{CALLER}", "can_use", f"tool:{tool.id}")),
    )
    before = await independent_capability_fingerprint(db, child, ctx)
    monkeypatch.setattr(runtime, "_current", enforce(model_permission))
    after = await independent_capability_fingerprint(db, child, ctx)
    assert before != after
    monkeypatch.setattr(runtime, "_current", enforce())
    with pytest.raises(AppError):
        await independent_capability_fingerprint(db, child, ctx)


async def test_company_default_credential_loses_organization_designation(
    db: AsyncSession, child: Agent
) -> None:
    ctx = unit_context(CALLER)
    # Model defaults can use designated company keys independently of their provider type.
    key = await _company(db, "dummy", orgs=(ctx.org_id,))
    model = await db.get(Model, child.model_id)
    assert model
    model.default_credential_id = key.id
    await db.commit()
    before = await independent_capability_fingerprint(db, child, ctx)
    await _designate(db, key, ())
    assert await independent_capability_fingerprint(db, child, ctx) != before


async def test_current_mcp_review_destination_and_user_block_change_manifest(
    db: AsyncSession, child: Agent
) -> None:
    ctx = unit_context(CALLER)
    server, mcp_tool = await add_server(db, owner=CALLER)
    db.add(AgentMcpToolLink(agent_id=child.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    before = await independent_capability_fingerprint(db, child, ctx)
    server.url = "https://changed.invalid/mcp"
    await db.commit()
    changed = await independent_capability_fingerprint(db, child, ctx)
    assert changed != before
    db.add(
        ResourceGrant(
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            resource_id=mcp_tool.id,
            resource_type="mcp_tool",
            relation="blocked",
            principal_type="user",
            principal_id=ctx.user_id,
        )
    )
    await db.commit()
    revoked = await independent_capability_fingerprint(db, child, ctx)
    assert revoked != changed


async def test_revoking_agent_delegation_to_model_credential_changes_pin(
    db: AsyncSession, child: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = unit_context(CALLER)
    key = await add_credential(db, owner=OWNER, definition_key="openai")
    child.llm_credential_id = key.id
    child.identity_mode = "fixed"
    await db.commit()
    model_permission = (f"user:{CALLER}", "can_use", f"llm_model:{child.model_id}")
    delegated = (f"agent:{child.id}", "can_use", f"credential:{key.id}")
    monkeypatch.setattr(runtime, "_current", enforce(model_permission, delegated))
    before = await independent_capability_fingerprint(db, child, ctx)
    monkeypatch.setattr(runtime, "_current", enforce(model_permission))
    assert await independent_capability_fingerprint(db, child, ctx) != before
