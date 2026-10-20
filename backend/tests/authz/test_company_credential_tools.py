"""Company shared credentials behind tools and MCP servers (personal-tenant plan P2-3, Q4).

Only the ``shared`` mode takes a company credential, and only while the company designates it
to the organization that attaches it or runs it; ``per_user`` connections stay personal.
"""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, with_designation
from app.authz.mcp_probe_scope import require_mcp_probe_scope
from app.authz.runtime import legacy_service
from app.credentials import tenant_encryption
from app.credentials.validation import require_credential_use
from app.exceptions import AppError
from app.models.credential import Credential
from app.models.organization import Tenant
from app.models.tool import AgentToolLink
from app.schemas.tool import ToolCreate
from app.services import tool_service
from tests.authz.d04_support import OTHER_ORG_ID, add_server, add_tool
from tests.authz.e01_support import add_agent, seed
from tests.authz.f05_support import CALLER, enforce, run_config
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context

ME = f"user:{CALLER}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _company(db: AsyncSession, value: str, *, orgs: tuple[UUID, ...]) -> Credential:
    credential = Credential(
        id=uuid4(),
        user_id=None,
        org_id=None,
        tenant_id=TEST_TENANT_ID,
        scope="tenant",
        definition_key="http_bearer",
        name="company bearer",
        is_shared=False,
        is_system=True,
        status="active",
    )
    await tenant_encryption.seal(credential, {"token": value}, db=db)
    db.add(credential)
    await _designate(db, credential, orgs)
    return credential


async def _designate(db: AsyncSession, credential: Credential, orgs: tuple[UUID, ...]) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = with_designation(tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, orgs)
    await db.commit()


async def _shared_tool_agent(db: AsyncSession, credential: Credential):  # noqa: ANN202
    agent = await add_agent(db, owner=CALLER)
    tool = await add_tool(db, owner=CALLER)
    tool.credential_mode, tool.credential_id = "shared", credential.id
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    return agent, tool


async def test_a_shared_tool_runs_on_a_designated_company_credential(db: AsyncSession) -> None:
    company = await _company(db, "company-token", orgs=(TEST_ORG_ID,))
    agent, _tool = await _shared_tool_agent(db, company)
    [entry] = await run_config(db, agent, legacy_service())
    assert entry["credentials"]["token"] == "company-token"
    assert entry["credential_id"] == str(company.id)


async def test_a_withdrawn_designation_stops_the_run_at_once(db: AsyncSession) -> None:
    company = await _company(db, "company-token", orgs=(TEST_ORG_ID,))
    agent, _tool = await _shared_tool_agent(db, company)
    await _designate(db, company, (OTHER_ORG_ID,))
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, legacy_service())
    assert caught.value.code == "CREDENTIAL_SUBJECT_MISMATCH"


async def test_enforced_runs_ask_can_use_for_the_user(db: AsyncSession) -> None:
    company = await _company(db, "company-token", orgs=(TEST_ORG_ID,))
    agent, tool = await _shared_tool_agent(db, company)
    base = ((ME, "can_run", f"agent:{agent.id}"), (ME, "can_use", f"tool:{tool.id}"))
    allowed = enforce(*base, (ME, "can_use", f"credential:{company.id}"))
    [entry] = await run_config(db, agent, allowed)
    assert entry["credentials"]["token"] == "company-token"
    with pytest.raises(AppError):
        await run_config(db, agent, enforce(*base))


async def test_attaching_needs_the_designation_to_the_callers_org(db: AsyncSession) -> None:
    designated = await _company(db, "a", orgs=(TEST_ORG_ID,))
    elsewhere = await _company(db, "b", orgs=(OTHER_ORG_ID,))
    context = unit_context(CALLER)
    created = await tool_service.create_tool(
        db,
        context=context,
        data=ToolCreate(definition_key="http_request", name="Company", credential_id=designated.id),
        authz=legacy_service(),
    )
    assert created.credential_id == designated.id
    with pytest.raises(HTTPException) as refused:
        await tool_service.create_tool(
            db,
            context=context,
            data=ToolCreate(definition_key="http_request", name="No", credential_id=elsewhere.id),
            authz=legacy_service(),
        )
    assert refused.value.status_code == 404


async def test_per_user_connections_never_take_a_company_credential(db: AsyncSession) -> None:
    company = await _company(db, "company-token", orgs=(TEST_ORG_ID,))
    with pytest.raises(HTTPException) as refused:
        await require_credential_use(
            db, credential_id=company.id, context=unit_context(CALLER), authz=legacy_service()
        )
    assert refused.value.status_code == 404


async def test_mcp_discovery_accepts_only_a_designated_company_credential(
    db: AsyncSession,
) -> None:
    designated = await _company(db, "a", orgs=(TEST_ORG_ID,))
    elsewhere = await _company(db, "b", orgs=(OTHER_ORG_ID,))
    server, _mcp_tool = await add_server(db, owner=CALLER)
    assert (await require_mcp_probe_scope(db, server, designated.id)) is not None
    with pytest.raises(AppError):
        await require_mcp_probe_scope(db, server, elsewhere.id)
