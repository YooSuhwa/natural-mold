"""Skill persistence + filesystem service.

Single source of truth for the new Skill domain. The legacy
``app/services/skill_service.py`` remains untouched for M5 deletion.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.skills.file_service import (
    delete_skill_file,
    get_file_bytes,
    get_skill_files,
    set_skill_file,
)
from app.skills.inspector import parse_skill_md
from app.skills.package_hash import compute_package_tree_hash
from app.skills.package_metadata import sync_frontmatter
from app.skills.packager import PackageError, extract_package
from app.storage.paths import ensure_relative, resolve_data_path

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


# -- helpers -----------------------------------------------------------------


def slugify(value: str) -> str:
    """Lowercase, dash-separated, ASCII-only identifier."""

    base = value.strip().lower().replace("_", "-").replace(" ", "-")
    cleaned = _SLUG_RE.sub("-", base).strip("-")
    return cleaned or "skill"


# ADR-018 — ``skills.storage_path`` is stored relative to ``settings.data_root``
# as ``skills/<id>`` (package) or ``skills/<id>/SKILL.md`` (text). The
# filesystem layout derives from ``data_root`` to match.


def _storage_root() -> Path:
    return (Path(settings.data_root) / "skills").resolve()


def _skill_root(skill_id: uuid.UUID) -> Path:
    return _storage_root() / str(skill_id)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- queries -----------------------------------------------------------------


async def list_skills(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: str | None = None,
    query: str | None = None,
) -> list[Skill]:
    stmt = select(Skill).where(Skill.user_id == user_id)
    if kind:
        stmt = stmt.where(Skill.kind == kind)
    if query:
        like = f"%{query.strip().lower()}%"
        stmt = stmt.where(func.lower(Skill.name).like(like))
    stmt = stmt.order_by(Skill.last_modified_at.desc(), Skill.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_skill(db: AsyncSession, skill_id: uuid.UUID, user_id: uuid.UUID) -> Skill | None:
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id))
    return result.scalar_one_or_none()


# -- mutations ---------------------------------------------------------------


async def create_text_skill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    slug: str | None,
    description: str | None,
    content: str,
    version: str | None = None,
) -> Skill:
    """Create a text skill, persisting ``content`` as ``SKILL.md`` on disk."""

    parse_skill_md(content, require_metadata=True)
    final_slug = slugify(slug or name)
    skill_id = uuid.uuid4()
    root = _skill_root(skill_id)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    file_path = root / "SKILL.md"
    body_bytes = content.encode("utf-8")
    await asyncio.to_thread(file_path.write_bytes, body_bytes)

    skill = Skill(
        id=skill_id,
        user_id=user_id,
        name=name,
        slug=final_slug,
        description=description,
        kind="text",
        storage_path=ensure_relative(f"skills/{skill_id}/SKILL.md"),
        content_hash=hashlib.sha256(body_bytes).hexdigest(),
        size_bytes=len(body_bytes),
        version=version,
        package_metadata=None,
        used_by_count=0,
        last_modified_at=_now(),
    )
    db.add(skill)
    await db.flush()
    return skill


async def create_package_skill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    zip_bytes: bytes,
    name_override: str | None = None,
    slug_override: str | None = None,
) -> Skill:
    """Extract a ``.skill`` zip and persist as a package skill."""

    skill_id = uuid.uuid4()
    root = _skill_root(skill_id)
    try:
        info = await asyncio.to_thread(extract_package, zip_bytes, root)
    except PackageError:
        # Cleanup partial directory on failure.
        await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
        raise

    final_name = name_override or info.name
    final_slug = slugify(slug_override or final_name)

    skill = Skill(
        id=skill_id,
        user_id=user_id,
        name=final_name,
        slug=final_slug,
        description=info.description,
        kind="package",
        storage_path=ensure_relative(f"skills/{skill_id}"),
        content_hash=await asyncio.to_thread(compute_package_tree_hash, root),
        size_bytes=info.total_bytes,
        version=info.version,
        package_metadata={
            "name": final_name,
            "version": info.version,
            "files": info.files,
            "has_scripts": info.has_scripts,
            "frontmatter": info.metadata,
        },
        used_by_count=0,
        # Task #15 / Spec §15.2 — ``.skill`` package uploads are external
        # artefacts the user brought in. The m41 backfill stamps existing
        # package rows ``imported_by_me``; new uploads must do the same
        # at creation time. Without this override the column default
        # ``created_by_me`` (m41) would mis-tag the row.
        origin_kind="imported_by_me",
        source_kind="import",
        last_modified_at=_now(),
    )
    db.add(skill)
    await db.flush()
    return skill


async def update_metadata(
    db: AsyncSession,
    *,
    skill: Skill,
    name: str | None = None,
    description: str | None = None,
    version: str | None = None,
) -> Skill:
    if name is not None and name != skill.name:
        skill.name = name
    if description is not None:
        skill.description = description
    if version is not None:
        skill.version = version
    skill.last_modified_at = _now()
    await db.flush()
    return skill


async def update_text_content(db: AsyncSession, *, skill: Skill, content: str) -> Skill:
    if skill.kind != "text":
        raise ValueError("update_text_content only valid for text skills")
    if not skill.storage_path:
        raise ValueError("text skill missing storage_path")
    parse_skill_md(content, require_metadata=True)
    body_bytes = content.encode("utf-8")
    await asyncio.to_thread(resolve_data_path(skill.storage_path).write_bytes, body_bytes)
    skill.content_hash = hashlib.sha256(body_bytes).hexdigest()
    skill.size_bytes = len(body_bytes)
    skill.last_modified_at = _now()
    sync_frontmatter(skill, body_bytes)
    await db.flush()
    return skill


async def delete_skill(db: AsyncSession, skill: Skill) -> None:
    """Remove the DB row and the on-disk storage.

    Both text and package skills live under ``<storage_dir>/<skill_id>/``;
    we wipe that directory regardless of kind. A missing path is a no-op.
    """

    storage = skill.storage_path
    await db.delete(skill)
    await db.flush()
    if not storage:
        return
    path = resolve_data_path(storage)
    # Text skills store the file at .../<id>/SKILL.md — delete the parent dir.
    is_file = await asyncio.to_thread(path.is_file)
    target = path.parent if is_file else path
    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)


# -- file access -------------------------------------------------------------


async def read_text_content(skill: Skill) -> str:
    """Read the body of a text skill from disk."""

    if skill.kind != "text" or not skill.storage_path:
        return ""
    path = resolve_data_path(skill.storage_path)
    is_file = await asyncio.to_thread(path.is_file)
    if not is_file:
        return ""
    return await asyncio.to_thread(path.read_text, "utf-8")


__all__: list[str] = [
    "create_package_skill",
    "create_text_skill",
    "delete_skill",
    "delete_skill_file",
    "get_file_bytes",
    "get_skill",
    "get_skill_files",
    "list_skills",
    "read_text_content",
    "set_skill_file",
    "slugify",
    "update_metadata",
    "update_text_content",
]


# Used by the runtime / agent prep — kept here to avoid a circular import.
def to_runtime_dict(skill: Skill) -> dict[str, Any]:
    """Serialize a skill to the dict deep-agents expects.

    ``description`` is included so :func:`app.skills.prompt.build_skills_prompt`
    can render a meaningful "Available Skills" block without re-fetching the
    ORM rows.
    """

    return {
        "id": str(skill.id),
        "name": skill.name,
        "slug": skill.slug,
        "kind": skill.kind,
        "storage_path": skill.storage_path or "",
        "description": skill.description or "",
    }
