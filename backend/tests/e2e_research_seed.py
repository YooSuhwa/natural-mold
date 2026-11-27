"""Restore the shipped 0.1.0 package in the empty, local research E2E database.

Run before the regular application starts. Its normal immutable marketplace seed
then publishes 0.2.0 on the same item. The browser installs and updates through
the normal endpoints. The frozen package is the original git blob 34bae7a0.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.config import settings
from app.database import engine, platform_session
from app.models.marketplace import MarketplaceItem, MarketplaceVersion


def _fixture_root() -> Path:
    url = make_url(settings.database_url)
    data_root = Path(settings.data_root).resolve()
    if (
        os.environ.get("MOLDY_RESEARCH_E2E") != "1"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("moldy_e2e_scripted_research_")
        or not data_root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        and not data_root.is_relative_to(Path("/tmp").resolve())
    ):
        raise RuntimeError("Expected a temporary local research E2E database and data directory")

    return data_root


def _restore_package(directory: Path) -> bytes:
    package = (Path(__file__).parent / "fixtures/deep-research-0.1.0/SKILL.md").read_bytes()
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(package)
    return package


async def seed_legacy_version(data_root: Path) -> None:
    async with platform_session("seed historical package in isolated research E2E") as db:
        item = await db.scalar(
            select(MarketplaceItem).where(MarketplaceItem.slug == "deep-research")
        )
        if item is not None:
            raise RuntimeError("Historical seed requires a fresh Deep Research catalog")
        item = MarketplaceItem(
            id=uuid4(),
            resource_type="skill",
            is_system=True,
            is_listed=True,
            name="Deep Research",
            slug="deep-research",
            description="Conduct multi-step, citation-backed web research with Tavily.",
            visibility="system",
            status="published",
            moderation_status="approved",
            source_kind="system_seed",
            source_external_id="deep-research",
            categories=["research"],
            tags=["research", "web", "citations", "tavily"],
            locale="ko",
        )
        db.add(item)
        await db.flush()
        version_id = uuid4()
        storage = Path("marketplace/system-skills") / str(version_id)
        package = await asyncio.to_thread(_restore_package, data_root / storage)
        db.add(
            MarketplaceVersion(
                id=version_id,
                item_id=item.id,
                version_label="0.1.0",
                version_number=1,
                resource_type="skill",
                payload_kind="skill_package",
                payload={
                    "kind": "package",
                    "name": "deep-research",
                    "version": "0.1.0",
                    "model": "auto",
                    "tool_dependencies": ["tavily_search"],
                },
                storage_path=storage.as_posix(),
                content_hash=hashlib.sha256(b"SKILL.md\0" + package).hexdigest(),
                size_bytes=len(package),
                credential_requirements=[],
                execution_profile={
                    "support_level": "ready_python",
                    "runners": ["python"],
                    "requires_network": True,
                    "tool_dependencies": ["tavily_search"],
                    "timeout_seconds": 420,
                    "notes": "Uses hosted Tavily Search through a runtime tool dependency; "
                    "no user credential binding is required.",
                },
                release_notes="Initial built-in Deep Research skill.",
                source_path="deep-research",
            )
        )
        await db.flush()
        item.latest_version_id = version_id
        item.published_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_legacy_version(_fixture_root()))
