"""Template → agent creation auto-installs recommended marketplace skills.

Covers the ``Template.recommended_skill_slugs`` path in
``agent_service.create_agent``: system marketplace skills referenced by the
template are installed for the user (re-using an existing installation) and
attached as ``AgentSkillLink`` rows. Seed drift (unknown slug) must degrade
to a warning, never a failed agent creation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceInstallation
from app.models.model import Model
from app.models.skill import Skill
from app.models.template import Template
from app.schemas.agent import AgentCreate
from app.services import agent_service
from tests.conftest import make_user


async def _seed_marketplace(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))
    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()


def _add_model(db: AsyncSession) -> Model:
    model = Model(provider="openai", model_name="gpt-test", display_name="GPT Test")
    db.add(model)
    return model


def _add_template(db: AsyncSession, slugs: list[str]) -> Template:
    template = Template(
        name=f"tmpl-{uuid.uuid4().hex[:6]}",
        category="개발",
        system_prompt="You document repositories.",
        recommended_skill_slugs=slugs,
    )
    db.add(template)
    return template


def _agent_create(model: Model, template: Template, name: str = "Doc Agent") -> AgentCreate:
    return AgentCreate(
        name=name,
        system_prompt="You document repositories.",
        model_id=model.id,
        template_id=template.id,
    )


@pytest.mark.asyncio
async def test_template_skill_slug_installs_and_attaches(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_marketplace(db, tmp_path, monkeypatch)
    user = await make_user(db)
    model = _add_model(db)
    template = _add_template(db, ["openwiki"])
    await db.flush()

    agent = await agent_service.create_agent(db, _agent_create(model, template), user.id)

    assert len(agent.skill_links) == 1
    skill = await db.get(Skill, agent.skill_links[0].skill_id)
    assert skill is not None
    assert skill.user_id == user.id
    assert skill.name == "openwiki"

    installations = (
        (
            await db.execute(
                select(MarketplaceInstallation).where(
                    MarketplaceInstallation.user_id == user.id,
                    MarketplaceInstallation.resource_type == "skill",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(installations) == 1
    assert installations[0].installed_skill_id == skill.id


@pytest.mark.asyncio
async def test_template_skill_reuses_existing_installation(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_marketplace(db, tmp_path, monkeypatch)
    user = await make_user(db)
    model = _add_model(db)
    template = _add_template(db, ["openwiki"])
    await db.flush()

    first = await agent_service.create_agent(
        db, _agent_create(model, template, name="Doc Agent 1"), user.id
    )
    second = await agent_service.create_agent(
        db, _agent_create(model, template, name="Doc Agent 2"), user.id
    )

    assert len(first.skill_links) == 1
    assert len(second.skill_links) == 1
    # reuse_or_update: 두 번째 생성은 새 Skill 사본을 만들지 않고 기존 설치를 재사용.
    assert first.skill_links[0].skill_id == second.skill_links[0].skill_id

    skills = (await db.execute(select(Skill).where(Skill.user_id == user.id))).scalars().all()
    assert len(skills) == 1


@pytest.mark.asyncio
async def test_template_with_unknown_skill_slug_still_creates_agent(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_marketplace(db, tmp_path, monkeypatch)
    user = await make_user(db)
    model = _add_model(db)
    template = _add_template(db, ["no-such-skill"])
    await db.flush()

    agent = await agent_service.create_agent(db, _agent_create(model, template), user.id)

    assert agent.skill_links == []


@pytest.mark.asyncio
async def test_template_without_skill_slugs_keeps_legacy_behavior(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_marketplace(db, tmp_path, monkeypatch)
    user = await make_user(db)
    model = _add_model(db)
    template = Template(
        name=f"tmpl-{uuid.uuid4().hex[:6]}",
        category="생산성",
        system_prompt="plain template",
    )
    db.add(template)
    await db.flush()

    agent = await agent_service.create_agent(db, _agent_create(model, template), user.id)

    assert agent.skill_links == []
