"""Skill bindings cannot select credentials from another private organization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace import credential_requirements as bindings
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.marketplace import SkillCredentialBinding
from app.models.model import Model
from app.models.skill import AgentSkillLink, Skill
from app.services import skill_response_enrichment as quality

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.fixture
async def binding_roots(
    db: AsyncSession, private_scopes: PrivateScopes
) -> tuple[Skill, Credential]:
    first, second = private_scopes.first, private_scopes.second
    skill = Skill(
        user_id=first.user_id,
        org_id=first.org_id,
        tenant_id=first.tenant_id,
        name="Binding fixture",
        slug="binding-fixture",
        kind="text",
        credential_requirements=[
            {
                "key": "credential",
                "definition_key": "openai_api",
                "required": True,
                "scope": "user",
                "injection": "env",
            }
        ],
    )
    credential = Credential(
        user_id=second.user_id,
        org_id=second.org_id,
        tenant_id=second.tenant_id,
        name="Foreign binding fixture",
        definition_key="openai_api",
        data_encrypted="unused-fixture",
        key_id="fixture",
        status="active",
    )
    db.add_all([skill, credential])
    await db.flush()
    return skill, credential


async def test_same_owner_foreign_credential_binding_is_rejected(
    db: AsyncSession, binding_roots: tuple[Skill, Credential], private_scopes: PrivateScopes
) -> None:
    # Given a private Skill and a same-owner Credential in another company.
    skill, credential = binding_roots
    # When the credential is proposed as a private Skill binding.
    with pytest.raises(AppError) as rejected:
        await bindings.upsert_binding(
            db,
            skill=skill,
            context=private_scopes.first,
            requirement_key="credential",
            credential_id=credential.id,
        )
    # Then the foreign credential is treated exactly like a missing credential.
    assert rejected.value.status == 404


async def test_foreign_binding_does_not_satisfy_required_setup(
    db: AsyncSession, binding_roots: tuple[Skill, Credential], private_scopes: PrivateScopes
) -> None:
    # Given a malformed persisted binding to a same-owner foreign credential.
    skill, credential = binding_roots
    db.add(
        SkillCredentialBinding(
            skill_id=skill.id,
            user_id=private_scopes.first.user_id,
            credential_id=credential.id,
            requirement_key="credential",
            scope="skill",
        )
    )
    await db.flush()
    # When installation readiness is projected for the Skill's company.
    missing = await bindings.missing_required_keys(db, skill=skill, context=private_scopes.first)
    # Then the foreign credential does not mark the installation ready.
    assert missing == ["credential"]


async def test_quality_projection_excludes_foreign_skill_and_credential(
    db: AsyncSession, binding_roots: tuple[Skill, Credential], private_scopes: PrivateScopes
) -> None:
    # Given a private Skill with an invalid foreign Credential reference.
    skill, credential = binding_roots
    db.add(
        SkillCredentialBinding(
            skill_id=skill.id,
            user_id=private_scopes.first.user_id,
            credential_id=credential.id,
            requirement_key="credential",
            scope="skill",
        )
    )
    await db.flush()
    # When quality metadata is requested in each company context.
    own = await quality.build_skill_quality_map(db, skills=[skill], context=private_scopes.first)
    foreign = await quality.build_skill_quality_map(
        db, skills=[skill], context=private_scopes.second
    )
    # Then invalid credentials stay missing and foreign Skill metadata is absent.
    assert own[skill.id].health.state == "needs_credentials"
    assert foreign == {}


async def test_foreign_agent_link_is_excluded_from_private_skill_usage_count(
    db: AsyncSession, binding_roots: tuple[Skill, Credential], private_scopes: PrivateScopes
) -> None:
    # Given a malformed link from another organization's same-owner Agent.
    skill, _credential = binding_roots
    context = private_scopes.second
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Foreign usage fixture",
        system_prompt="Fixture",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    db.add(AgentSkillLink(agent_id=agent.id, skill_id=skill.id))
    await db.flush()
    # When usage count is requested in the Skill's organization.
    counts = await quality.agent_link_counts_by_skill(
        db, skill_ids=[skill.id], context=private_scopes.first
    )
    # Then the foreign Agent does not count as local usage.
    assert counts == {}
