"""Tests for ``app.agent_runtime.credential_resolution``.

The resolver picks the API key the runtime hands to ChatOpenAI / ChatAnthropic.
The 3rd tier — auto-match by ``model.provider`` → ``credential.definition_key`` —
must include ``openai_compatible`` so users who only register a self-hosted
endpoint credential (without explicit agent binding) don't hit a 422 wall.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_resolution import (
    LLMCredentialRequiredError,
    resolve_llm_api_key_for_agent,
)
from app.agent_runtime.identity import (
    AGENT_IDENTITY_FIXED,
    AGENT_IDENTITY_PER_USER,
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID

OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


async def _make_agent_with_model(
    db: AsyncSession,
    *,
    provider: str,
    base_url: str | None = None,
) -> Agent:
    model = Model(
        provider=provider,
        model_name="local-llm",
        display_name="Local LLM",
        base_url=base_url,
    )
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=TEST_USER_ID,
        name="Resolver Agent",
        system_prompt="hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.commit()

    fresh = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
    # Force the lazy-loaded relationships the resolver expects.
    await db.refresh(fresh, attribute_names=["model", "llm_credential"])
    return fresh


@pytest.mark.asyncio
async def test_auto_match_openai_compatible_user_credential(
    db: AsyncSession,
) -> None:
    """When the agent has no explicit binding and the user owns an
    ``openai_compatible`` credential, the resolver auto-matches it via
    ``PROVIDER_TO_DEFINITION_KEY`` and returns the decrypted ``api_key``."""

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai_compatible",
        name="my-self-hosted",
        data={"base_url": "https://my-host/v1", "api_key": "sk-local-key"},
    )
    await db.commit()

    agent = await _make_agent_with_model(
        db, provider="openai_compatible", base_url="https://my-host/v1"
    )

    key = await resolve_llm_api_key_for_agent(db, agent)

    assert key == "sk-local-key"


@pytest.mark.asyncio
async def test_auto_match_missing_credential_raises(db: AsyncSession) -> None:
    """With no credential of any kind, the resolver raises 422 — regression
    guard so the new ``openai_compatible`` entry doesn't accidentally fall
    through to env fallback for super_users (ADR-016 §4.2)."""

    agent = await _make_agent_with_model(db, provider="openai_compatible")

    with pytest.raises(LLMCredentialRequiredError):
        await resolve_llm_api_key_for_agent(db, agent)


@pytest.mark.asyncio
async def test_per_user_identity_resolves_provider_match_for_caller_subject(
    db: AsyncSession,
) -> None:
    other_user_uuid = __import__("uuid").UUID(OTHER_USER_ID)
    db.add(User(id=other_user_uuid, email="other@test.com", name="Other User"))
    await credential_service.create(
        db,
        user_id=other_user_uuid,
        definition_key="openai_compatible",
        name="caller-key",
        data={"base_url": "https://caller/v1", "api_key": "sk-caller"},
    )
    await db.commit()

    agent = await _make_agent_with_model(db, provider="openai_compatible")
    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=make_agent_runtime_name(agent.id),
        identity_mode=AGENT_IDENTITY_PER_USER,
        source=AgentRunSource.CHAT,
        caller_user_id=other_user_uuid,
    )

    key = await resolve_llm_api_key_for_agent(db, agent, identity=identity)

    assert key == "sk-caller"


@pytest.mark.asyncio
async def test_explicit_agent_llm_credential_must_belong_to_identity_subject(
    db: AsyncSession,
) -> None:
    owner_cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai_compatible",
        name="owner-key",
        data={"base_url": "https://owner/v1", "api_key": "sk-owner"},
    )
    other_user_uuid = __import__("uuid").UUID(OTHER_USER_ID)
    db.add(User(id=other_user_uuid, email="other@test.com", name="Other User"))
    await db.commit()

    agent = await _make_agent_with_model(db, provider="openai_compatible")
    agent.llm_credential_id = owner_cred.id
    await db.commit()
    await db.refresh(agent, attribute_names=["model", "llm_credential"])

    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=make_agent_runtime_name(agent.id),
        identity_mode=AGENT_IDENTITY_PER_USER,
        source=AgentRunSource.CHAT,
        caller_user_id=other_user_uuid,
    )

    with pytest.raises(LLMCredentialRequiredError):
        await resolve_llm_api_key_for_agent(db, agent, identity=identity)


@pytest.mark.asyncio
async def test_fixed_identity_keeps_owner_subject_for_explicit_credential(
    db: AsyncSession,
) -> None:
    owner_cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai_compatible",
        name="owner-key",
        data={"base_url": "https://owner/v1", "api_key": "sk-owner"},
    )
    await db.commit()

    agent = await _make_agent_with_model(db, provider="openai_compatible")
    agent.llm_credential_id = owner_cred.id
    await db.commit()
    await db.refresh(agent, attribute_names=["model", "llm_credential"])
    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=make_agent_runtime_name(agent.id),
        identity_mode=AGENT_IDENTITY_FIXED,
        source=AgentRunSource.CHAT,
        caller_user_id=uuid.UUID(OTHER_USER_ID),
    )

    key = await resolve_llm_api_key_for_agent(db, agent, identity=identity)

    assert key == "sk-owner"


@pytest.mark.asyncio
async def test_auto_match_skips_system_credentials(db: AsyncSession) -> None:
    """The auto-match path is explicitly user-credential-only — system
    rows are reserved for service flows (builder/assistant). A bare
    system credential must not satisfy an end-user chat resolution."""

    await credential_service.create(
        db,
        user_id=None,
        definition_key="openai_compatible",
        name="system-only",
        data={"base_url": "https://shared/v1", "api_key": "sk-system"},
        is_system=True,
    )
    await db.commit()

    agent = await _make_agent_with_model(db, provider="openai_compatible")

    with pytest.raises(LLMCredentialRequiredError):
        await resolve_llm_api_key_for_agent(db, agent)
