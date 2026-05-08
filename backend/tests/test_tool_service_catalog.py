"""``app.services.tool_service.get_tools_catalog`` 회귀 가드.

Builder phase3 추천기는 이 함수가 노출하는 모든 종류 (Tool / McpTool / Skill)
를 LLM 입력으로 받는다. 한 종류라도 누락되면 사용자가 명시적으로 요청해도
LLM 이 해당 카테고리를 보지 못해 silent drop.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.models.tool import Tool
from app.models.user import User
from app.services.tool_service import get_tools_catalog
from tests.conftest import TEST_USER_ID


async def _seed_user(db: AsyncSession) -> None:
    db.add(User(id=TEST_USER_ID, email="t@t.com", name="T"))
    await db.flush()


@pytest.mark.asyncio
async def test_catalog_includes_tools_mcp_and_skills(db: AsyncSession):
    await _seed_user(db)

    db.add(
        Tool(name="Web Search", definition_key="builtin:web_search", description="d")
    )
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Hancom",
        transport="sse",
        url="https://example.com/mcp",
    )
    db.add(server)
    await db.flush()
    db.add(McpTool(server_id=server.id, name="list_departments", description="d"))
    db.add(
        Skill(
            user_id=TEST_USER_ID,
            name="seat_layout_guide",
            slug="seat-layout-guide",
            description="좌석",
        )
    )
    await db.commit()

    items = await get_tools_catalog(db, TEST_USER_ID)
    by_kind: dict[str, list[str]] = {"tool": [], "mcp": [], "skill": []}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item["name"])
    assert "Web Search" in by_kind["tool"]
    assert "list_departments" in by_kind["mcp"]
    assert "seat_layout_guide" in by_kind["skill"]


@pytest.mark.asyncio
async def test_catalog_skills_filtered_by_user(db: AsyncSession):
    """다른 사용자의 skill 은 catalog 에서 제외 (cross-user 노출 방지)."""
    await _seed_user(db)
    other_id = uuid.uuid4()
    db.add(User(id=other_id, email="o@o.com", name="O"))
    await db.flush()
    db.add(
        Skill(
            user_id=other_id,
            name="other_skill",
            slug="other-skill",
            description="d",
        )
    )
    db.add(
        Skill(
            user_id=TEST_USER_ID,
            name="my_skill",
            slug="my-skill",
            description="d",
        )
    )
    await db.commit()

    items = await get_tools_catalog(db, TEST_USER_ID)
    skill_names = {it["name"] for it in items if it["kind"] == "skill"}
    assert skill_names == {"my_skill"}
