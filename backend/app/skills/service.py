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
from app.skills.inspector import (
    FileInfo,
    _resolve_safely,
    list_files,
    parse_skill_md,
    read_file_safe,
)
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


async def get_skill(
    db: AsyncSession, skill_id: uuid.UUID, user_id: uuid.UUID
) -> Skill | None:
    result = await db.execute(
        select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id)
    )
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
        content_hash=info.content_hash,
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


async def update_text_content(
    db: AsyncSession, *, skill: Skill, content: str
) -> Skill:
    if skill.kind != "text":
        raise ValueError("update_text_content only valid for text skills")
    if not skill.storage_path:
        raise ValueError("text skill missing storage_path")
    body_bytes = content.encode("utf-8")
    await asyncio.to_thread(
        resolve_data_path(skill.storage_path).write_bytes, body_bytes
    )
    skill.content_hash = hashlib.sha256(body_bytes).hexdigest()
    skill.size_bytes = len(body_bytes)
    skill.last_modified_at = _now()
    _sync_frontmatter(skill, body_bytes)
    await db.flush()
    return skill


# -- file-level mutations (M-SKILL1) -----------------------------------------


def _package_root(skill: Skill) -> Path:
    """Resolve the package skill's storage root, raising if misconfigured."""

    if skill.kind != "package" or not skill.storage_path:
        raise ValueError("file-level mutations require a package skill")
    return resolve_data_path(skill.storage_path)


async def set_skill_file(
    db: AsyncSession,
    *,
    skill: Skill,
    rel_path: str,
    content: bytes,
) -> Skill:
    """Create or overwrite a file inside a package skill's storage root.

    Path is resolved through :func:`_resolve_safely` so traversal / absolute
    paths are rejected. Parent directories are created on demand.
    """

    root = _package_root(skill)
    target = _resolve_safely(root, rel_path)
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, content)
    _refresh_package_metadata(skill)
    skill.last_modified_at = _now()
    # If the edit touched SKILL.md, propagate frontmatter into model fields.
    if rel_path.lstrip("./").lower() in {"skill.md", "skill.markdown"}:
        _sync_frontmatter(skill, content)
    await db.flush()
    return skill


async def delete_skill_file(
    db: AsyncSession,
    *,
    skill: Skill,
    rel_path: str,
) -> Skill:
    """Delete a file inside a package skill's storage root.

    SKILL.md is protected — refuse the request to keep the package valid.
    """

    cleaned = rel_path.lstrip("./").lower()
    if cleaned in {"skill.md", "skill.markdown"}:
        raise ValueError("SKILL.md cannot be deleted")
    root = _package_root(skill)
    target = _resolve_safely(root, rel_path)
    if target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
    else:
        await asyncio.to_thread(target.unlink, missing_ok=True)
    _refresh_package_metadata(skill)
    skill.last_modified_at = _now()
    await db.flush()
    return skill


def _refresh_package_metadata(skill: Skill) -> None:
    """Recompute size + cached file list after a file mutation."""

    if skill.kind != "package" or not skill.storage_path:
        return
    files = list_files(resolve_data_path(skill.storage_path))
    skill.size_bytes = sum(f.size for f in files if not f.is_dir)
    meta = dict(skill.package_metadata or {})
    meta["files"] = [f.path for f in files if not f.is_dir]
    skill.package_metadata = meta


def _sync_frontmatter(skill: Skill, body: bytes) -> None:
    """Parse SKILL.md frontmatter into model fields (best-effort)."""

    try:
        parsed = parse_skill_md(body)
    except Exception:  # noqa: BLE001 — malformed frontmatter is user data
        return
    metadata = parsed.get("metadata") or {}
    if not isinstance(metadata, dict):
        return
    if isinstance(metadata.get("description"), str):
        skill.description = metadata["description"]
    if isinstance(metadata.get("version"), str):
        skill.version = metadata["version"]
    if isinstance(metadata.get("name"), str) and not skill.name.strip():
        skill.name = metadata["name"]
    pkg = dict(skill.package_metadata or {})
    pkg["frontmatter"] = metadata
    skill.package_metadata = pkg


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


def get_skill_files(skill: Skill) -> list[FileInfo]:
    """Enumerate package skill files (text skills return a single entry)."""

    if not skill.storage_path:
        return []
    if skill.kind == "text":
        path = resolve_data_path(skill.storage_path)
        if not path.is_file():
            return []
        return [FileInfo(path="SKILL.md", size=path.stat().st_size, is_dir=False)]
    # Package skills: storage_path is the root directory.
    return list_files(resolve_data_path(skill.storage_path))


def get_file_bytes(skill: Skill, rel_path: str) -> bytes:
    """Read a single file from the skill storage with traversal protection."""

    if not skill.storage_path:
        raise FileNotFoundError("skill has no storage")
    if skill.kind == "text":
        if rel_path not in {"SKILL.md", ""}:
            raise FileNotFoundError(rel_path)
        return resolve_data_path(skill.storage_path).read_bytes()
    return read_file_safe(resolve_data_path(skill.storage_path), rel_path)


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
