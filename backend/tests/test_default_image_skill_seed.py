from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.models.skill import Skill
from app.storage.paths import resolve_data_path


@pytest.mark.asyncio
async def test_seed_default_image_skill_creates_system_marketplace_item(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()

    item = (
        await db.execute(
            select(MarketplaceItem).where(
                MarketplaceItem.source_kind == "system_seed",
                MarketplaceItem.source_external_id == "image-generation",
            )
        )
    ).scalar_one()
    assert item.is_system is True
    assert item.is_listed is True
    assert item.visibility == "system"
    assert item.status == "published"
    assert item.latest_version_id is not None

    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    assert version.payload["name"] == "image-generation"
    assert version.payload["model"] == "auto"
    assert version.payload["model_defaults"] == {
        "openai_compatible": "gpt-image-2",
        "openrouter": "openai/gpt-5.4-image-2",
    }
    assert version.storage_path is not None
    assert version.storage_path.startswith("marketplace/system-skills/")
    assert (resolve_data_path(version.storage_path) / "SKILL.md").exists()

    assert version.credential_requirements == [
        {
            "key": "image_endpoint",
            "definition_key": "openai_compatible",
            "required": True,
            "label": "Image generation endpoint",
            "description": "OpenAI-compatible image generation endpoint.",
            "fields": ["base_url", "api_key"],
            "injection": "env",
            "scope": "user",
            "env_map": {
                "base_url": "IMAGE_API_BASE_URL",
                "api_key": "IMAGE_API_KEY",
            },
        }
    ]
    assert version.execution_profile == {
        "support_level": "ready_python",
        "runners": ["python"],
        "requires_python": True,
        "timeout_seconds": 420,
    }


@pytest.mark.asyncio
async def test_seed_default_image_skill_is_idempotent(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()

    items = (
        (
            await db.execute(
                select(MarketplaceItem).where(
                    MarketplaceItem.source_kind == "system_seed",
                    MarketplaceItem.source_external_id == "image-generation",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(items) == 1

    versions = (
        (
            await db.execute(
                select(MarketplaceVersion).where(MarketplaceVersion.item_id == items[0].id)
            )
        )
        .scalars()
        .all()
    )
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_seed_default_deep_research_skill_creates_tool_dependency_profile(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()

    item = (
        await db.execute(
            select(MarketplaceItem).where(
                MarketplaceItem.source_kind == "system_seed",
                MarketplaceItem.source_external_id == "deep-research",
            )
        )
    ).scalar_one()
    assert item.is_system is True
    assert item.is_listed is True
    assert item.visibility == "system"
    assert item.status == "published"
    assert item.slug == "deep-research"
    assert item.latest_version_id is not None

    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    assert version.payload["name"] == "deep-research"
    assert version.version_label == version.payload["version"] == "0.2.0"
    assert version.payload["tool_dependencies"] == ["research_search"]
    assert version.storage_path is not None
    assert version.storage_path.startswith("marketplace/system-skills/")
    assert (resolve_data_path(version.storage_path) / "SKILL.md").exists()
    assert version.credential_requirements == []
    assert version.execution_profile == {
        "support_level": "ready_python",
        "runners": ["python"],
        "requires_network": True,
        "tool_dependencies": ["research_search"],
        "timeout_seconds": 420,
        "notes": (
            "Uses organization-approved search providers and preserves original source URLs; "
            "no user credential binding is required."
        ),
    }


@pytest.mark.asyncio
async def test_seed_default_deep_research_skill_is_idempotent(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()

    items = (
        (
            await db.execute(
                select(MarketplaceItem).where(
                    MarketplaceItem.source_kind == "system_seed",
                    MarketplaceItem.source_external_id == "deep-research",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(items) == 1

    versions = (
        (
            await db.execute(
                select(MarketplaceVersion).where(MarketplaceVersion.item_id == items[0].id)
            )
        )
        .scalars()
        .all()
    )
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_installed_deep_research_keeps_tavily_until_explicit_update(
    client: AsyncClient, db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))
    item = MarketplaceItem(
        resource_type="skill",
        is_system=True,
        is_listed=True,
        name="Deep Research",
        slug="deep-research",
        source_kind="system_seed",
        source_external_id="deep-research",
        visibility="system",
        status="published",
    )
    db.add(item)
    await db.flush()
    legacy_id = uuid.uuid4()
    storage = f"marketplace/system-skills/{legacy_id}"
    legacy_file = tmp_path / storage / "SKILL.md"
    await asyncio.to_thread(legacy_file.parent.mkdir, parents=True)
    legacy_prompt = "---\nname: deep-research\nversion: 0.1.0\n---\nUse `tavily_search`."
    await asyncio.to_thread(legacy_file.write_text, legacy_prompt)
    legacy_profile = {"requires_network": True, "tool_dependencies": ["tavily_search"]}
    legacy = MarketplaceVersion(
        id=legacy_id,
        item_id=item.id,
        version_label="0.1.0",
        version_number=1,
        resource_type="skill",
        payload_kind="skill_package",
        payload={"kind": "package", "name": "deep-research", "version": "0.1.0"},
        storage_path=storage,
        content_hash=await asyncio.to_thread(
            default_marketplace_skills._content_hash, legacy_file.parent
        ),
        size_bytes=len(legacy_prompt),
        credential_requirements=[],
        execution_profile=legacy_profile,
    )
    db.add(legacy)
    await db.flush()
    item.latest_version_id = legacy.id
    await db.commit()
    installed = await client.post(f"/api/marketplace/items/{item.id}/install", json={})
    assert installed.status_code == 201, installed.text
    installation = installed.json()
    skill = await db.get(Skill, uuid.UUID(installation["installed_skill_id"]))
    assert skill is not None and skill.storage_path is not None
    installed_file = resolve_data_path(skill.storage_path) / "SKILL.md"

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()
    await db.refresh(skill)
    assert item.latest_version_id != legacy_id
    assert skill.source_marketplace_version_id == legacy_id
    assert skill.execution_profile == legacy_profile
    assert await asyncio.to_thread(installed_file.read_text) == legacy_prompt
    detail = await client.get(f"/api/marketplace/items/{item.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["installation"]["update_available"] is True

    updated = await client.post(
        f"/api/marketplace/installations/{installation['id']}/update",
        json={"strategy": "overwrite"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["installed_skill_id"] == str(skill.id)
    await db.refresh(skill)
    assert skill.source_marketplace_version_id == item.latest_version_id
    assert skill.version == "0.2.0"
    assert skill.execution_profile["tool_dependencies"] == ["research_search"]
    assert "`research_search`" in await asyncio.to_thread(installed_file.read_text)
    assert await asyncio.to_thread(legacy_file.read_text) == legacy_prompt
