from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.storage.paths import resolve_data_path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("slug", "runner", "extension"),
    [
        ("docx-document", "node", "docx"),
        ("xlsx-spreadsheet", "node", "xlsx"),
        ("pptx-presentation", "node", "pptx"),
        ("patent-hwpx-generator", "python", "hwpx"),
        ("openwiki", "python", "md"),
    ],
)
async def test_seed_default_document_skills_create_system_items(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    runner: str,
    extension: str,
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()

    item = (
        await db.execute(
            select(MarketplaceItem).where(
                MarketplaceItem.source_kind == "system_seed",
                MarketplaceItem.source_external_id == slug,
            )
        )
    ).scalar_one()
    assert item.is_system is True
    assert item.is_listed is True
    assert item.visibility == "system"
    assert item.status == "published"
    assert item.slug == slug
    assert item.latest_version_id is not None

    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    assert version.payload["name"] == slug
    assert version.payload["kind"] == "package"
    assert runner in version.execution_profile["runners"]
    assert extension in version.payload["artifact_extensions"]
    assert version.credential_requirements == []
    assert version.storage_path is not None
    assert version.storage_path.startswith("marketplace/system-skills/")
    assert (resolve_data_path(version.storage_path) / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_seed_default_document_skills_are_idempotent(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))

    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await db.commit()

    for spec in default_marketplace_skills.DOCUMENT_SKILL_SPECS:
        slug = spec["slug"]
        items = (
            (
                await db.execute(
                    select(MarketplaceItem).where(
                        MarketplaceItem.source_kind == "system_seed",
                        MarketplaceItem.source_external_id == slug,
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
