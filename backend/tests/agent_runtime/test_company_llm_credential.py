"""Company shared LLM keys in agent chat (personal-tenant plan P2-1/P2-2, Q1).

Order: agent key → model default (own, or a company key designated to the run's org) →
own provider key → company provider key designated to the run's org. Platform keys never.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import (
    LLMCredentialRequiredError,
    resolve_llm_api_key_for_agent,
    resolved_llm_credential_scope,
)
from app.agent_runtime.identity import (
    AGENT_IDENTITY_FIXED,
    AgentRunSource,
    resolve_agent_run_identity,
)
from app.authz import runtime
from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, with_designation
from app.authz.service import AuthzService
from app.credentials import tenant_encryption
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.model import Model
from app.models.organization import Tenant
from app.models.user import User
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    create_test_credential,
    ensure_unit_scope,
    unit_context,
)
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    if await db.get(User, TEST_USER_ID) is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()
    await ensure_unit_scope(db, CurrentUser(id=TEST_USER_ID, email="test@test.com", name="T"))
    await seed_scope(db)
    await db.commit()


async def _company_key(
    db: AsyncSession, api_key: str, *, orgs: tuple[uuid.UUID, ...], status: str = "active"
) -> Credential:
    credential = Credential(
        id=uuid.uuid4(),
        user_id=None,
        org_id=None,
        tenant_id=TEST_TENANT_ID,
        scope="tenant",
        definition_key="openai",
        name=f"company-{api_key}",
        is_shared=False,
        is_system=True,
        status=status,
    )
    await tenant_encryption.seal(credential, {"api_key": api_key}, db=db)
    db.add(credential)
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = with_designation(tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, orgs)
    await db.commit()
    return credential


async def _personal_key(db: AsyncSession, api_key: str) -> Credential:
    credential = await create_test_credential(
        db,
        CredentialCreation(
            definition_key="openai", name=f"own-{api_key}", data={"api_key": api_key}
        ),
        unit_context(TEST_USER_ID),
    )
    await db.commit()
    return credential


async def _agent(db: AsyncSession, *, default: Credential | None = None) -> Agent:
    model = Model(
        provider="openai",
        model_name=f"gpt-{uuid.uuid4().hex[:6]}",
        display_name="GPT",
        default_credential_id=None if default is None else default.id,
    )
    db.add(model)
    await db.flush()
    agent = Agent(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        name="Company key agent",
        system_prompt="hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.commit()
    fresh = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
    await db.refresh(fresh, attribute_names=["model", "llm_credential"])
    return fresh


def _access(agent: Agent) -> AgentCredentialAccess:
    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name,
        identity_mode=AGENT_IDENTITY_FIXED,
        source=AgentRunSource.CHAT,
        caller_user_id=TEST_USER_ID,
    )
    return AgentCredentialAccess(unit_context(TEST_USER_ID), identity)


async def _resolve(db: AsyncSession, agent: Agent) -> str | None:
    return await resolve_llm_api_key_for_agent(db, agent, _access(agent))


async def test_company_key_designated_to_the_run_org_serves_a_member_without_a_key(
    db: AsyncSession,
) -> None:
    await _company_key(db, "sk-company", orgs=(TEST_ORG_ID,))
    assert await _resolve(db, await _agent(db)) == "sk-company"
    assert resolved_llm_credential_scope() == "tenant"


@pytest.mark.parametrize(
    ("orgs", "status"),
    [((), "active"), ((OTHER_ORG_ID,), "active"), ((TEST_ORG_ID,), "disabled")],
    ids=["undesignated", "other-org", "disabled"],
)
async def test_company_key_outside_the_run_org_or_inactive_is_never_used(
    db: AsyncSession, orgs: tuple[uuid.UUID, ...], status: str
) -> None:
    await _company_key(db, "sk-company", orgs=orgs, status=status)
    with pytest.raises(LLMCredentialRequiredError):
        await _resolve(db, await _agent(db))
    assert resolved_llm_credential_scope() is None


async def test_own_provider_key_comes_before_the_company_key(db: AsyncSession) -> None:
    await _company_key(db, "sk-company", orgs=(TEST_ORG_ID,))
    await _personal_key(db, "sk-own")
    assert await _resolve(db, await _agent(db)) == "sk-own"
    assert resolved_llm_credential_scope() == "personal"


async def test_company_key_set_as_the_model_default_comes_before_the_own_key(
    db: AsyncSession,
) -> None:
    company = await _company_key(db, "sk-company", orgs=(TEST_ORG_ID,))
    await _personal_key(db, "sk-own")
    assert await _resolve(db, await _agent(db, default=company)) == "sk-company"


async def test_undesignated_company_model_default_falls_through_to_the_own_key(
    db: AsyncSession,
) -> None:
    company = await _company_key(db, "sk-company", orgs=(OTHER_ORG_ID,))
    await _personal_key(db, "sk-own")
    assert await _resolve(db, await _agent(db, default=company)) == "sk-own"


async def test_platform_key_set_as_the_model_default_is_never_used(db: AsyncSession) -> None:
    platform = Credential(
        id=uuid.uuid4(),
        user_id=None,
        org_id=None,
        tenant_id=None,
        scope="platform",
        definition_key="openai",
        name="operator",
        is_shared=False,
        is_system=True,
        status="active",
    )
    await tenant_encryption.seal(platform, {"api_key": "sk-operator"}, db=db)
    db.add(platform)
    await db.commit()
    with pytest.raises(LLMCredentialRequiredError):
        await _resolve(db, await _agent(db, default=platform))


async def test_enforced_mode_asks_can_use_for_the_user_not_the_agent(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = await _company_key(db, "sk-company", orgs=(TEST_ORG_ID,))
    agent = await _agent(db)
    client = RecordingClient(
        allowed=frozenset(
            {
                (f"user:{TEST_USER_ID}", "can_use", f"credential:{company.id}"),
            }
        )
    )
    monkeypatch.setattr(runtime, "_current", AuthzService(client, mode="enforce"))  # type: ignore[arg-type]
    assert await _resolve(db, agent) == "sk-company"
    # Without the tuple (e.g. a designation removed in OpenFGA) enforced mode refuses it.
    denied = AuthzService(RecordingClient(allowed=frozenset()), mode="enforce")  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_current", denied)
    with pytest.raises(LLMCredentialRequiredError):
        await _resolve(db, agent)
