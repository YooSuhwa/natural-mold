from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template


@pytest.mark.asyncio
async def test_list_templates(client: AsyncClient, db: AsyncSession):
    db.add(
        Template(
            name="Test Template",
            description="A test template",
            category="생산성",
            system_prompt="You are a test assistant.",
            recommended_tools=["Gmail"],
            usage_example="Test me",
        )
    )
    await db.commit()

    resp = await client.get("/api/templates")
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) == 1
    assert templates[0]["name"] == "Test Template"


@pytest.mark.asyncio
async def test_list_templates_by_category(client: AsyncClient, db: AsyncSession):
    db.add(Template(name="T1", category="생산성", system_prompt="p1"))
    db.add(Template(name="T2", category="데이터", system_prompt="p2"))
    await db.commit()

    resp = await client.get("/api/templates?category=생산성")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "T1"


@pytest.mark.asyncio
async def test_get_template_not_found(client: AsyncClient):
    resp = await client.get("/api/templates/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404
