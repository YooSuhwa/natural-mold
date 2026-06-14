from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.skills.inspector import (
    FileInfo,
    _resolve_safely,
    list_files,
    parse_skill_md,
    read_file_safe,
)
from app.skills.package_metadata import refresh_package_metadata, sync_frontmatter
from app.storage.paths import resolve_data_path


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _package_root(skill: Skill) -> str:
    if skill.kind != "package" or not skill.storage_path:
        raise ValueError("file-level mutations require a package skill")
    return skill.storage_path


async def set_skill_file(
    db: AsyncSession,
    *,
    skill: Skill,
    rel_path: str,
    content: bytes,
) -> Skill:
    storage_path = _package_root(skill)
    root = resolve_data_path(storage_path)
    target = _resolve_safely(root, rel_path)
    if rel_path.lstrip("./").lower() in {"skill.md", "skill.markdown"}:
        parse_skill_md(content, require_metadata=True)
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, content)
    refresh_package_metadata(skill)
    skill.last_modified_at = _now()
    if rel_path.lstrip("./").lower() in {"skill.md", "skill.markdown"}:
        sync_frontmatter(skill, content)
    await db.flush()
    return skill


async def delete_skill_file(
    db: AsyncSession,
    *,
    skill: Skill,
    rel_path: str,
) -> Skill:
    cleaned = rel_path.lstrip("./").lower()
    if cleaned in {"skill.md", "skill.markdown"}:
        raise ValueError("SKILL.md cannot be deleted")
    storage_path = _package_root(skill)
    root = resolve_data_path(storage_path)
    target = _resolve_safely(root, rel_path)
    if target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
    else:
        await asyncio.to_thread(target.unlink, missing_ok=True)
    refresh_package_metadata(skill)
    skill.last_modified_at = _now()
    await db.flush()
    return skill


def get_skill_files(skill: Skill) -> list[FileInfo]:
    if not skill.storage_path:
        return []
    if skill.kind == "text":
        path = resolve_data_path(skill.storage_path)
        if not path.is_file():
            return []
        return [FileInfo(path="SKILL.md", size=path.stat().st_size, is_dir=False)]
    return list_files(resolve_data_path(skill.storage_path))


def get_file_bytes(skill: Skill, rel_path: str) -> bytes:
    if not skill.storage_path:
        raise FileNotFoundError("skill has no storage")
    if skill.kind == "text":
        if rel_path not in {"SKILL.md", ""}:
            raise FileNotFoundError(rel_path)
        return resolve_data_path(skill.storage_path).read_bytes()
    return read_file_safe(resolve_data_path(skill.storage_path), rel_path)
