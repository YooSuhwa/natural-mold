from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.schemas.skill import SkillCreate, SkillUpdate
from app.services import skill_service
from tests.conftest import TEST_USER_ID

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _create_skill_via_api(client: AsyncClient, **overrides) -> dict:
    payload = {
        "name": "테스트 스킬",
        "description": "테스트 설명",
        "content": "테스트 콘텐츠 내용",
        **overrides,
    }
    resp = await client.post("/api/skills", json=payload)
    assert resp.status_code == 201
    return resp.json()


async def _seed_skill(db: AsyncSession, **overrides) -> Skill:
    skill = Skill(
        user_id=overrides.get("user_id", TEST_USER_ID),
        name=overrides.get("name", "DB 스킬"),
        description=overrides.get("description", "설명"),
        content=overrides.get("content", "콘텐츠"),
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


# ===========================================================================
# Router 테스트 (API 레벨)
# ===========================================================================


class TestSkillsRouter:
    """Skills CRUD API 엔드포인트 테스트."""

    @pytest.mark.asyncio
    async def test_create_skill(self, client: AsyncClient):
        data = await _create_skill_via_api(client)
        assert data["name"] == "테스트 스킬"
        assert data["description"] == "테스트 설명"
        assert data["content"] == "테스트 콘텐츠 내용"
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_create_skill_without_description(self, client: AsyncClient):
        data = await _create_skill_via_api(client, name="No Desc", description=None, content="내용")
        assert data["description"] is None

    @pytest.mark.asyncio
    async def test_list_skills_empty(self, client: AsyncClient):
        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_skills(self, client: AsyncClient):
        await _create_skill_via_api(client, name="스킬1")
        await _create_skill_via_api(client, name="스킬2")

        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        skills = resp.json()
        assert len(skills) == 2

    @pytest.mark.asyncio
    async def test_list_skills_ordered_by_updated_at_desc(self, client: AsyncClient):
        await _create_skill_via_api(client, name="먼저 만든 스킬")
        await _create_skill_via_api(client, name="나중에 만든 스킬")

        resp = await client.get("/api/skills")
        skills = resp.json()
        assert skills[0]["name"] == "나중에 만든 스킬"
        assert skills[1]["name"] == "먼저 만든 스킬"

    @pytest.mark.asyncio
    async def test_get_skill(self, client: AsyncClient):
        created = await _create_skill_via_api(client)
        skill_id = created["id"]

        resp = await client.get(f"/api/skills/{skill_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "테스트 스킬"

    @pytest.mark.asyncio
    async def test_get_skill_not_found(self, client: AsyncClient):
        fake_id = "00000000-0000-0000-0000-000000000099"
        resp = await client.get(f"/api/skills/{fake_id}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Skill not found"

    @pytest.mark.asyncio
    async def test_update_skill_name(self, client: AsyncClient):
        created = await _create_skill_via_api(client)
        skill_id = created["id"]

        resp = await client.put(
            f"/api/skills/{skill_id}",
            json={"name": "변경된 이름"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "변경된 이름"
        assert resp.json()["content"] == "테스트 콘텐츠 내용"  # 나머지 유지

    @pytest.mark.asyncio
    async def test_update_skill_all_fields(self, client: AsyncClient):
        created = await _create_skill_via_api(client)
        skill_id = created["id"]

        resp = await client.put(
            f"/api/skills/{skill_id}",
            json={
                "name": "새 이름",
                "description": "새 설명",
                "content": "새 콘텐츠",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "새 이름"
        assert data["description"] == "새 설명"
        assert data["content"] == "새 콘텐츠"

    @pytest.mark.asyncio
    async def test_update_skill_not_found(self, client: AsyncClient):
        fake_id = "00000000-0000-0000-0000-000000000099"
        resp = await client.put(
            f"/api/skills/{fake_id}",
            json={"name": "nope"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_skill(self, client: AsyncClient):
        created = await _create_skill_via_api(client)
        skill_id = created["id"]

        resp = await client.delete(f"/api/skills/{skill_id}")
        assert resp.status_code == 204

        resp = await client.get(f"/api/skills/{skill_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_skill_not_found(self, client: AsyncClient):
        fake_id = "00000000-0000-0000-0000-000000000099"
        resp = await client.delete(f"/api/skills/{fake_id}")
        assert resp.status_code == 404


# ===========================================================================
# Service 테스트 (비즈니스 로직 레벨)
# ===========================================================================


class TestSkillService:
    """skill_service 함수 직접 테스트."""

    @pytest.mark.asyncio
    async def test_create_skill(self, db: AsyncSession):
        data = SkillCreate(name="서비스 스킬", description="desc", content="body")
        skill = await skill_service.create_skill(db, data, TEST_USER_ID)

        assert skill.name == "서비스 스킬"
        assert skill.user_id == TEST_USER_ID
        assert skill.id is not None

    @pytest.mark.asyncio
    async def test_list_skills_only_own(self, db: AsyncSession):
        await _seed_skill(db, name="내 스킬", user_id=TEST_USER_ID)
        await _seed_skill(db, name="남의 스킬", user_id=OTHER_USER_ID)

        skills = await skill_service.list_skills(db, TEST_USER_ID)
        assert len(skills) == 1
        assert skills[0].name == "내 스킬"

    @pytest.mark.asyncio
    async def test_get_skill_own(self, db: AsyncSession):
        skill = await _seed_skill(db, user_id=TEST_USER_ID)
        found = await skill_service.get_skill(db, skill.id, TEST_USER_ID)
        assert found is not None
        assert found.id == skill.id

    @pytest.mark.asyncio
    async def test_get_skill_other_user_returns_none(self, db: AsyncSession):
        skill = await _seed_skill(db, user_id=OTHER_USER_ID)
        found = await skill_service.get_skill(db, skill.id, TEST_USER_ID)
        assert found is None

    @pytest.mark.asyncio
    async def test_update_skill_partial(self, db: AsyncSession):
        skill = await _seed_skill(db, name="원래", description="원래 설명", content="원래 콘텐츠")
        updated = await skill_service.update_skill(db, skill, SkillUpdate(name="변경됨"))
        assert updated.name == "변경됨"
        assert updated.description == "원래 설명"  # 변경 안 됨
        assert updated.content == "원래 콘텐츠"  # 변경 안 됨

    @pytest.mark.asyncio
    async def test_update_skill_all(self, db: AsyncSession):
        skill = await _seed_skill(db)
        updated = await skill_service.update_skill(
            db,
            skill,
            SkillUpdate(name="새", description="새 설명", content="새 콘텐츠"),
        )
        assert updated.name == "새"
        assert updated.description == "새 설명"
        assert updated.content == "새 콘텐츠"

    @pytest.mark.asyncio
    async def test_delete_skill(self, db: AsyncSession):
        skill = await _seed_skill(db)
        await skill_service.delete_skill(db, skill)

        found = await skill_service.get_skill(db, skill.id, TEST_USER_ID)
        assert found is None
