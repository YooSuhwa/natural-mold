from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.marketplace.secret_scan import scan_package
from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.storage.paths import ensure_relative

_PACKAGE_ROOT = Path(__file__).parent / "system_skill_packages"
_IMAGE_SKILL_DIR = _PACKAGE_ROOT / "image-generation"
_DEEP_RESEARCH_SKILL_DIR = _PACKAGE_ROOT / "deep-research"

IMAGE_SKILL_REQUIREMENTS: list[dict[str, Any]] = [
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

IMAGE_SKILL_EXECUTION_PROFILE: dict[str, Any] = {
    "support_level": "ready_python",
    "runners": ["python"],
    "requires_python": True,
    "timeout_seconds": 420,
}

DEEP_RESEARCH_EXECUTION_PROFILE: dict[str, Any] = {
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


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _system_storage_root() -> Path:
    return (Path(settings.data_root) / "marketplace" / "system-skills").resolve()


def _copytree(src: Path, dest: Path) -> int:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, symlinks=False)
    total = 0
    for entry in dest.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def _content_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        if not entry.is_file():
            continue
        rel = entry.relative_to(path).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        try:
            hasher.update(entry.read_bytes())
        except OSError:
            continue
    return hasher.hexdigest()


async def _next_version_number(db: AsyncSession, item_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(MarketplaceVersion.version_number), 0)).where(
            MarketplaceVersion.item_id == item_id
        )
    )
    return int(result.scalar_one() or 0) + 1


async def seed_default_marketplace_skills(db: AsyncSession) -> list[MarketplaceItem]:
    """Seed built-in marketplace skills that ship with the application.

    The seed creates immutable marketplace versions from package bytes under
    ``app/seed/system_skill_packages``. Re-running it only creates a new version
    when the package content hash changes.
    """

    seeded: list[MarketplaceItem] = []
    seeded.append(
        await _seed_image_generation_skill(
            db,
            skill_dir=_IMAGE_SKILL_DIR,
            storage_root=_system_storage_root(),
        )
    )
    seeded.append(
        await _seed_deep_research_skill(
            db,
            skill_dir=_DEEP_RESEARCH_SKILL_DIR,
            storage_root=_system_storage_root(),
        )
    )
    return seeded


async def _seed_image_generation_skill(
    db: AsyncSession, *, skill_dir: Path, storage_root: Path
) -> MarketplaceItem:
    findings = await asyncio.to_thread(scan_package, skill_dir)
    if findings:
        summary = ", ".join(f"{f.path} ({f.kind})" for f in findings[:5])
        raise RuntimeError(f"default image skill contains potential secrets: {summary}")

    content_hash = await asyncio.to_thread(_content_hash, skill_dir)
    item = (
        await db.execute(
            select(MarketplaceItem)
            .where(MarketplaceItem.resource_type == "skill")
            .where(MarketplaceItem.is_system.is_(True))
            .where(
                or_(
                    MarketplaceItem.source_external_id == "image-generation",
                    MarketplaceItem.slug == "image-generation",
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if item is None:
        item = MarketplaceItem(
            id=uuid.uuid4(),
            resource_type="skill",
            owner_user_id=None,
            is_system=True,
            is_listed=True,
            name="Image Generation",
            slug="image-generation",
            description="Generate images through a user-provided OpenAI-compatible endpoint.",
            visibility="system",
            status="published",
            moderation_status="approved",
            source_kind="system_seed",
            source_external_id="image-generation",
            categories=["image"],
            tags=["image", "generation"],
            locale="ko",
        )
        db.add(item)
        await db.flush()
    else:
        item.name = "Image Generation"
        item.description = "Generate images through a user-provided OpenAI-compatible endpoint."
        item.is_listed = True
        item.source_kind = "system_seed"
        item.source_external_id = "image-generation"
        item.visibility = "system"
        item.status = "published"
        item.updated_at = _now()

    existing_version = (
        await db.execute(
            select(MarketplaceVersion)
            .where(MarketplaceVersion.item_id == item.id)
            .where(MarketplaceVersion.content_hash == content_hash)
            .order_by(MarketplaceVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_version is not None:
        item.latest_version_id = existing_version.id
        item.published_at = item.published_at or _now()
        await db.flush()
        return item

    version_id = uuid.uuid4()
    version_dest = storage_root / str(version_id)
    total_bytes = await asyncio.to_thread(_copytree, skill_dir, version_dest)
    version_number = await _next_version_number(db, item.id)

    version = MarketplaceVersion(
        id=version_id,
        item_id=item.id,
        version_label="0.1.0",
        version_number=version_number,
        resource_type="skill",
        payload_kind="skill_package",
        payload={
            "kind": "package",
            "name": "image-generation",
            "version": "0.1.0",
            "model": "auto",
            "model_defaults": {
                "openai_compatible": "gpt-image-2",
                "openrouter": "openai/gpt-5.4-image-2",
            },
            "provider_adapter": "openai_compatible",
        },
        storage_path=ensure_relative(f"marketplace/system-skills/{version_id}"),
        content_hash=content_hash,
        size_bytes=total_bytes,
        credential_requirements=IMAGE_SKILL_REQUIREMENTS,
        execution_profile=IMAGE_SKILL_EXECUTION_PROFILE,
        release_notes="Initial built-in image generation skill.",
        source_path="image-generation",
        created_by=None,
    )
    db.add(version)
    await db.flush()

    item.latest_version_id = version.id
    item.published_at = item.published_at or _now()
    item.updated_at = _now()
    await db.flush()
    return item


async def _seed_deep_research_skill(
    db: AsyncSession, *, skill_dir: Path, storage_root: Path
) -> MarketplaceItem:
    findings = await asyncio.to_thread(scan_package, skill_dir)
    if findings:
        summary = ", ".join(f"{f.path} ({f.kind})" for f in findings[:5])
        raise RuntimeError(f"default deep research skill contains potential secrets: {summary}")

    content_hash = await asyncio.to_thread(_content_hash, skill_dir)
    item = (
        await db.execute(
            select(MarketplaceItem)
            .where(MarketplaceItem.resource_type == "skill")
            .where(MarketplaceItem.is_system.is_(True))
            .where(
                or_(
                    MarketplaceItem.source_external_id == "deep-research",
                    MarketplaceItem.slug == "deep-research",
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if item is None:
        item = MarketplaceItem(
            id=uuid.uuid4(),
            resource_type="skill",
            owner_user_id=None,
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
    else:
        item.name = "Deep Research"
        item.description = "Conduct multi-step, citation-backed web research with Tavily."
        item.is_listed = True
        item.source_kind = "system_seed"
        item.source_external_id = "deep-research"
        item.visibility = "system"
        item.status = "published"
        item.updated_at = _now()

    existing_version = (
        await db.execute(
            select(MarketplaceVersion)
            .where(MarketplaceVersion.item_id == item.id)
            .where(MarketplaceVersion.content_hash == content_hash)
            .order_by(MarketplaceVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_version is not None:
        item.latest_version_id = existing_version.id
        item.published_at = item.published_at or _now()
        await db.flush()
        return item

    version_id = uuid.uuid4()
    version_dest = storage_root / str(version_id)
    total_bytes = await asyncio.to_thread(_copytree, skill_dir, version_dest)
    version_number = await _next_version_number(db, item.id)

    version = MarketplaceVersion(
        id=version_id,
        item_id=item.id,
        version_label="0.1.0",
        version_number=version_number,
        resource_type="skill",
        payload_kind="skill_package",
        payload={
            "kind": "package",
            "name": "deep-research",
            "version": "0.1.0",
            "model": "auto",
            "tool_dependencies": ["tavily_search"],
        },
        storage_path=ensure_relative(f"marketplace/system-skills/{version_id}"),
        content_hash=content_hash,
        size_bytes=total_bytes,
        credential_requirements=[],
        execution_profile=DEEP_RESEARCH_EXECUTION_PROFILE,
        release_notes="Initial built-in Deep Research skill.",
        source_path="deep-research",
        created_by=None,
    )
    db.add(version)
    await db.flush()

    item.latest_version_id = version.id
    item.published_at = item.published_at or _now()
    item.updated_at = _now()
    await db.flush()
    return item


__all__ = [
    "DEEP_RESEARCH_EXECUTION_PROFILE",
    "IMAGE_SKILL_EXECUTION_PROFILE",
    "IMAGE_SKILL_REQUIREMENTS",
    "seed_default_marketplace_skills",
]
