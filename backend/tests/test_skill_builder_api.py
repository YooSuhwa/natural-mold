from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.conversation import Conversation
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID
from tests.skill_builder_test_helpers import configure_system_llm as _configure_system_llm

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """start v2가 드래프트 워크스페이스를 디스크에 만들므로 data_root 격리."""

    monkeypatch.setattr(settings, "data_root", str(tmp_path))


def _skill_content(name: str = "notes") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        "Use when summarizing meeting notes.\n"
    )


def _draft_payload() -> dict[str, object]:
    return {
        "name": "Notes",
        "slug": "notes",
        "description": "Use when summarizing notes into action items.",
        "files": [
            {"path": "SKILL.md", "content": _skill_content(), "role": "skill"},
            {
                "path": "agents/openai.yaml",
                "content": 'interface:\n  default_prompt: "$notes summarize"\n',
                "role": "metadata",
            },
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


async def test_start_requires_system_llm(client: AsyncClient) -> None:
    response = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SYSTEM_LLM_NOT_CONFIGURED"


async def test_start_create_session(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _configure_system_llm(db)

    response = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "create"
    # v2: 세션은 빌더 챗 상태 기계의 ACTIVE로 시작한다.
    assert body["status"] == "active"
    assert body["source_skill_id"] is None
    # v2: 히든 에이전트의 draft conversation + 워크스페이스가 붙는다.
    assert body["conversation_id"] is not None
    assert body["agent_id"] is not None
    assert (tmp_path / "skill-drafts" / body["id"]).is_dir()

    conversation = await db.get(Conversation, uuid.UUID(body["conversation_id"]))
    assert conversation is not None
    assert conversation.source == "draft"
    assert str(conversation.agent_id) == body["agent_id"]

    session = await db.get(SkillBuilderSession, uuid.UUID(body["id"]))
    assert session is not None
    assert session.draft_workspace_path == f"skill-drafts/{body['id']}"


async def test_start_lazy_seeds_hidden_agent_once(
    client: AsyncClient, db: AsyncSession
) -> None:
    """두 번 시작해도 히든 빌더 에이전트는 사용자당 1개만 시드된다."""

    await _configure_system_llm(db)

    first = await client.post(
        BASE, json={"mode": "create", "user_request": "스킬 하나"}
    )
    second = await client.post(
        BASE, json={"mode": "create", "user_request": "스킬 둘"}
    )

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["agent_id"] == second.json()["agent_id"]
    assert first.json()["conversation_id"] != second.json()["conversation_id"]

    result = await db.execute(
        select(Agent).where(
            Agent.user_id == TEST_USER_ID,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
        )
    )
    hidden_agents = list(result.scalars().all())
    assert len(hidden_agents) == 1
    assert str(hidden_agents[0].id) == first.json()["agent_id"]

    # 히든 에이전트는 에이전트 목록에 노출되지 않는다.
    agents = await client.get("/api/agents")
    assert first.json()["agent_id"] not in {a["id"] for a in agents.json()}


async def test_get_session_returns_agent_and_conversation(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _configure_system_llm(db)

    start = await client.post(
        BASE, json={"mode": "create", "user_request": "회의록 스킬"}
    )
    session_id = start.json()["id"]

    response = await client.get(f"{BASE}/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == start.json()["conversation_id"]
    assert body["agent_id"] == start.json()["agent_id"]


async def test_start_improve_session_snapshots_owned_skill(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )
        await db.commit()

        response = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "더 정확하게 개선해줘",
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "improve"
    assert body["source_skill_id"] == str(skill.id)
    assert body["base_content_hash"] == skill.content_hash
    assert body["base_snapshot"]["files"][0]["path"] == "SKILL.md"


async def test_start_improve_unowned_skill_returns_404(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=uuid.uuid4(),
            name="Other",
            slug="other",
            description="Use when summarizing notes.",
            content=_skill_content("other"),
        )
        await db.commit()

        response = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "개선해줘",
            },
        )

    assert response.status_code == 404


async def test_validate_persists_result(client: AsyncClient, db: AsyncSession) -> None:
    await _configure_system_llm(db)
    start = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
    )
    session_id = start.json()["id"]

    response = await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft_package"]["slug"] == "notes"
    assert body["validation_result"]["error_count"] == 0
    assert body["compatibility_result"]["targets"]


async def test_confirm_returns_skill_response(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        start = await client.post(
            BASE,
            json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
        )
        session_id = start.json()["id"]
        await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

        response = await client.post(f"{BASE}/{session_id}/confirm")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "package"
    assert body["slug"] == "notes"


async def test_confirm_completed_session_is_idempotent(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        start = await client.post(
            BASE,
            json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
        )
        session_id = start.json()["id"]
        await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

        first = await client.post(f"{BASE}/{session_id}/confirm")
        second = await client.post(f"{BASE}/{session_id}/confirm")

    result = await db.execute(
        select(Skill).where(Skill.user_id == TEST_USER_ID, Skill.slug == "notes")
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(result.scalars().all()) == 1


async def test_confirm_improve_conflict_returns_409(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )
        await db.commit()
        start = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "개선해줘",
            },
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content() + "\nManual change.\n",
        )
        await db.commit()
        session_id = start.json()["id"]
        await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

        response = await client.post(f"{BASE}/{session_id}/confirm")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SKILL_BUILDER_SOURCE_CONFLICT"
