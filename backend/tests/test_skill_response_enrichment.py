from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.marketplace import SkillCredentialBinding
from app.models.skill import Skill
from app.services import skill_response_enrichment


@dataclass(frozen=True, slots=True)
class _User:
    id: uuid.UUID


def _skill(*, user_id: uuid.UUID, slug: str, requirement_key: str = "openai") -> Skill:
    return Skill(
        id=uuid.uuid4(),
        user_id=user_id,
        name=slug,
        slug=slug,
        description="Use when testing skill quality enrichment.",
        kind="text",
        content_hash="a" * 64,
        size_bytes=1,
        credential_requirements=[
            {
                "key": requirement_key,
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI",
                "fields": ["api_key"],
                "scope": "user",
            }
        ],
    )


@pytest.mark.asyncio
async def test_build_skill_quality_map_batches_required_credential_bindings(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _User(uuid.uuid4())
    missing_skill = _skill(user_id=user.id, slug="missing")
    bound_skill = _skill(user_id=user.id, slug="bound")
    db.add_all([missing_skill, bound_skill])
    credential = await credential_service.create(
        db,
        user_id=user.id,
        definition_key="openai",
        name="OpenAI",
        data={"api_key": "sk-test"},
    )
    db.add(
        SkillCredentialBinding(
            skill_id=bound_skill.id,
            user_id=user.id,
            requirement_key="openai",
            credential_id=credential.id,
            scope="skill",
        )
    )
    await db.flush()

    async def fail_per_skill_lookup(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError("per-skill credential lookup should not run")

    monkeypatch.setattr(
        skill_response_enrichment.credential_requirements,
        "missing_required_keys",
        fail_per_skill_lookup,
    )

    summaries = await skill_response_enrichment.build_skill_quality_map(
        db,
        user=user,
        skills=[missing_skill, bound_skill],
    )

    assert summaries[missing_skill.id].health.state == "needs_credentials"
    assert summaries[bound_skill.id].health.state == "needs_evaluation"
