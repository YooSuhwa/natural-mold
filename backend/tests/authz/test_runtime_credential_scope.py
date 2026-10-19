"""All runtime credential tiers require the exact current organization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import (
    LLMCredentialRequiredError,
    resolve_llm_api_key_for_agent,
)
from app.agent_runtime.identity import (
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.agent import Agent
from app.models.model import Model

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class RuntimeCase:
    tier: Literal["explicit", "model_default", "provider"]
    foreign: bool


@pytest.mark.parametrize(
    "case",
    [
        RuntimeCase(tier, foreign)
        for tier in ("explicit", "model_default", "provider")
        for foreign in (False, True)
    ],
)
async def test_runtime_credential_tiers_follow_exact_current_scope(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    case: RuntimeCase,
) -> None:
    # Given: a credential owned by this user in company B, while the run is in A.
    foreign = await credential_service.create(
        db,
        CredentialCreation(
            definition_key="openai",
            name="Foreign runtime fixture",
            data={"api_key": "dummy-foreign-only"},
        ),
        private_scopes.second if case.foreign else private_scopes.first,
    )
    await db.commit()
    agent_id = uuid4()
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    agent = Agent(
        id=agent_id,
        user_id=private_scopes.first.user_id,
        name="Runtime fixture",
        system_prompt="Fixture",
        model=model,
    )
    match case.tier:
        case "explicit":
            agent.llm_credential = foreign
        case "model_default":
            model.default_credential_id = foreign.id
        case "provider":
            pass
        case unreachable:
            assert_never(unreachable)
    identity = resolve_agent_run_identity(
        agent_id=agent_id,
        agent_owner_user_id=private_scopes.first.user_id,
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode="fixed",
        source=AgentRunSource.CHAT,
        caller_user_id=private_scopes.first.user_id,
    )
    access = AgentCredentialAccess(private_scopes.first, identity)
    # When: the runtime resolves the selected tier in organization A.
    if case.foreign:
        with pytest.raises(LLMCredentialRequiredError):
            await resolve_llm_api_key_for_agent(db, agent, access)
    else:
        assert await resolve_llm_api_key_for_agent(db, agent, access) == "dummy-foreign-only"
    # Then: none of the tiers can decrypt or select the same user's B credential.
