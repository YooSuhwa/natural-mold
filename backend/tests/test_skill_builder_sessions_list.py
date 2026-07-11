"""빌더 세션 목록 API (Phase 2 — 스튜디오 빌더 탭/인덱스).

user 스코프, skill_id의 source/finalized 양방향 매칭, 상태 필터,
updated_at 내림차순 정렬 계약을 검증한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_builder_session import SkillBuilderSession
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000cc")


def _session(
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    mode: str = "create",
    status: str = "active",
    source_skill_id: uuid.UUID | None = None,
    finalized_skill_id: uuid.UUID | None = None,
    updated_at: datetime | None = None,
    user_request: str = "회의록 스킬 만들어줘",
) -> SkillBuilderSession:
    session = SkillBuilderSession(
        user_id=user_id,
        user_request=user_request,
        mode=mode,
        status=status,
        source_skill_id=source_skill_id,
        finalized_skill_id=finalized_skill_id,
    )
    if updated_at is not None:
        session.created_at = updated_at
        session.updated_at = updated_at
    return session


async def test_list_scopes_to_user_and_orders_desc(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    older = _session(updated_at=datetime(2026, 7, 1, 9, 0), user_request="old")
    newer = _session(updated_at=datetime(2026, 7, 10, 9, 0), user_request="new")
    foreign = _session(user_id=OTHER_USER_ID, user_request="foreign")
    db.add_all([older, newer, foreign])
    await db.commit()

    response = await client.get(BASE)

    assert response.status_code == 200, response.text
    body = response.json()
    ids = [row["id"] for row in body]
    assert ids == [str(newer.id), str(older.id)]
    assert str(foreign.id) not in ids
    # 경량 brief 계약 — 무거운 JSON 컬럼은 싣지 않는다.
    assert set(body[0]) == {
        "id",
        "mode",
        "status",
        "user_request",
        "source_skill_id",
        "finalized_skill_id",
        "conversation_id",
        "created_at",
        "updated_at",
    }


async def test_skill_id_matches_source_and_finalized(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    skill_id = uuid.uuid4()
    improve = _session(mode="improve", source_skill_id=skill_id)
    created = _session(status="completed", finalized_skill_id=skill_id)
    unrelated = _session()
    db.add_all([improve, created, unrelated])
    await db.commit()

    response = await client.get(BASE, params={"skill_id": str(skill_id)})

    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()}
    assert ids == {str(improve.id), str(created.id)}


async def test_status_filter_and_validation(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    active = _session(status="active")
    completed = _session(status="completed")
    db.add_all([active, completed])
    await db.commit()

    filtered = await client.get(BASE, params={"status": "completed"})
    assert filtered.status_code == 200, filtered.text
    assert [row["id"] for row in filtered.json()] == [str(completed.id)]

    invalid = await client.get(BASE, params={"status": "bogus"})
    assert invalid.status_code == 422

    bad_limit = await client.get(BASE, params={"limit": 101})
    assert bad_limit.status_code == 422


async def test_limit_caps_results(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    db.add_all([_session(updated_at=datetime(2026, 7, day, 9, 0)) for day in range(1, 5)])
    await db.commit()

    response = await client.get(BASE, params={"limit": 2})

    assert response.status_code == 200, response.text
    assert len(response.json()) == 2
