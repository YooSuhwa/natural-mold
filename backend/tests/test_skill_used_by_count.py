"""used_by_count 실집계 (Phase 2) — serializer가 agent_skills 역집계로 덮어쓴다.

컬럼 자체는 쓰기 동기화가 없어 항상 0 — API 응답만 실카운트여야 하고,
히든 에이전트(runtime_profile != 'standard')와 타 유저 에이전트는 제외된다.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.skill import AgentSkillLink, Skill
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _skill_content() -> str:
    return (
        "---\n"
        "name: notes\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        "Use when summarizing meeting notes.\n"
    )


def _agent(user_id: uuid.UUID, *, runtime_profile: str | None = None) -> Agent:
    kwargs: dict[str, object] = {}
    if runtime_profile is not None:
        kwargs["runtime_profile"] = runtime_profile
    return Agent(
        id=uuid.uuid4(),
        user_id=user_id,
        name=f"agent-{uuid.uuid4().hex[:6]}",
        description=None,
        system_prompt="",
        model_id=uuid.uuid4(),
        **kwargs,
    )


async def _make_skill(db: AsyncSession, tmp_path: Path) -> Skill:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        return await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )


async def test_list_and_detail_return_live_link_count(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _make_skill(db, tmp_path)
    first = _agent(TEST_USER_ID)
    second = _agent(TEST_USER_ID)
    db.add_all([first, second])
    await db.flush()
    db.add_all(
        [
            AgentSkillLink(agent_id=first.id, skill_id=skill.id),
            AgentSkillLink(agent_id=second.id, skill_id=skill.id),
        ]
    )
    await db.commit()

    listed = await client.get("/api/skills")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["id"] == str(skill.id))
    assert row["used_by_count"] == 2

    detail = await client.get(f"/api/skills/{skill.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["used_by_count"] == 2

    # 컬럼은 여전히 0 — serializer 주입이지 컬럼 동기화가 아니다.
    await db.refresh(skill)
    assert skill.used_by_count == 0


async def test_hidden_and_foreign_agent_links_excluded(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _make_skill(db, tmp_path)
    hidden = _agent(TEST_USER_ID, runtime_profile=AGENT_RUNTIME_PROFILE_SKILL_BUILDER)
    foreign = _agent(OTHER_USER_ID)
    db.add_all([hidden, foreign])
    await db.flush()
    db.add_all(
        [
            AgentSkillLink(agent_id=hidden.id, skill_id=skill.id),
            AgentSkillLink(agent_id=foreign.id, skill_id=skill.id),
        ]
    )
    await db.commit()

    listed = await client.get("/api/skills")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["id"] == str(skill.id))
    assert row["used_by_count"] == 0


async def test_unlinked_skill_counts_zero(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _make_skill(db, tmp_path)
    await db.commit()

    detail = await client.get(f"/api/skills/{skill.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["used_by_count"] == 0
