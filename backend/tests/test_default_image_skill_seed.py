from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceItem, MarketplaceVersion
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
    assert version.storage_path is not None
    assert version.storage_path.startswith("marketplace/system-skills/")
    assert (resolve_data_path(version.storage_path) / "SKILL.md").exists()
    assert version.credential_requirements == []
    assert version.execution_profile == {
        "support_level": "ready_python",
        "runners": ["python"],
        "requires_network": True,
        "tool_dependencies": ["tavily_search"],
        "timeout_seconds": 420,
        "notes": (
            "Uses hosted Tavily Search through a runtime tool dependency; "
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
