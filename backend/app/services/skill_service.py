from __future__ import annotations

import io
import os
import uuid
import zipfile
from pathlib import Path

import frontmatter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.schemas.skill import SkillCreate, SkillUpdate


async def list_skills(db: AsyncSession, user_id: uuid.UUID) -> list[Skill]:
    result = await db.execute(
        select(Skill).where(Skill.user_id == user_id).order_by(Skill.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_skill(db: AsyncSession, skill_id: uuid.UUID, user_id: uuid.UUID) -> Skill | None:
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id))
    return result.scalar_one_or_none()


async def create_skill(db: AsyncSession, data: SkillCreate, user_id: uuid.UUID) -> Skill:
    skill = Skill(
        user_id=user_id,
        name=data.name,
        description=data.description,
        content=data.content,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


async def update_skill(db: AsyncSession, skill: Skill, data: SkillUpdate) -> Skill:
    if data.name is not None:
        skill.name = data.name
    if data.description is not None:
        skill.description = data.description
    if data.content is not None:
        skill.content = data.content
    await db.commit()
    await db.refresh(skill)
    return skill


async def delete_skill(db: AsyncSession, skill: Skill) -> None:
    await db.delete(skill)
    await db.commit()


# ---------------------------------------------------------------------------
# Package skill upload
# ---------------------------------------------------------------------------


def _find_skill_md(zf: zipfile.ZipFile) -> str | None:
    """Find SKILL.md at root or one directory level deep."""
    for name in zf.namelist():
        parts = Path(name).parts
        basename = parts[-1] if parts else ""
        if basename == "SKILL.md" and len(parts) <= 2:
            return name
    return None


def _validate_zip_entry(member: zipfile.ZipInfo, dest: Path) -> Path:
    """Validate a ZIP entry against zip-slip, absolute paths, and symlinks."""
    # Reject symlinks (external_attr bit 29 = symlink on Unix)
    if member.external_attr >> 16 & 0o120000 == 0o120000:
        raise ValueError(f"Symlink not allowed: {member.filename}")

    target = (dest / member.filename).resolve()
    if not str(target).startswith(str(dest)):
        raise ValueError(f"Path traversal detected: {member.filename}")
    if os.path.isabs(member.filename):
        raise ValueError(f"Absolute path not allowed: {member.filename}")
    return target


async def upload_skill_package(
    db: AsyncSession,
    file_data: bytes,
    user_id: uuid.UUID,
) -> Skill:
    """Parse a .skill ZIP, extract files, and create a package Skill."""
    if len(file_data) > settings.skill_max_package_bytes:
        raise ValueError(
            f"Package too large: {len(file_data)} bytes (max {settings.skill_max_package_bytes})"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file") from exc

    with zf:
        # 1. Find SKILL.md
        skill_md_path = _find_skill_md(zf)
        if not skill_md_path:
            raise ValueError("SKILL.md not found in archive (root or 1-level subdir)")

        # 2. Parse frontmatter
        raw_md = zf.read(skill_md_path).decode("utf-8")
        post = frontmatter.loads(raw_md)
        name = post.metadata.get("name") or Path(skill_md_path).parent.name or "Untitled"
        description = post.metadata.get("description", "")
        content = post.content  # body without frontmatter

        # 3. Determine prefix (subdir containing SKILL.md)
        prefix = str(Path(skill_md_path).parent)
        if prefix == ".":
            prefix = ""

        # 4. Check for scripts
        has_scripts = False
        for entry in zf.namelist():
            rel = entry[len(prefix) :].lstrip("/") if prefix else entry
            if rel.startswith("scripts/") and rel.endswith(".py"):
                has_scripts = True
                break

        # 5. Create DB record first to get ID
        skill = Skill(
            user_id=user_id,
            name=name,
            description=description,
            content=content,
            type="package",
        )
        db.add(skill)
        await db.flush()  # get skill.id

        # 6. Extract files
        dest = Path(settings.skill_storage_dir).resolve() / str(skill.id)
        dest.mkdir(parents=True, exist_ok=True)

        for member in zf.infolist():
            if member.is_dir():
                continue
            _validate_zip_entry(member, dest)

            # Strip prefix so files land directly under dest/
            rel = member.filename[len(prefix) :].lstrip("/") if prefix else member.filename
            if not rel:
                continue
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest):
                raise ValueError(f"Path traversal detected: {rel}")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member.filename))

        skill.storage_path = str(dest)
        await db.commit()
        await db.refresh(skill)

    # Attach computed property for response
    skill._has_scripts = has_scripts  # type: ignore[attr-defined]
    return skill
