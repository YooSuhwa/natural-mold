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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scoped
from app.config import settings as settings  # tests patch ``skill_service.settings``
from app.error_codes import skill_not_found
from app.models.skill import Skill
from app.skills.creation import PackageSkillCreation, TextSkillCreation
from app.skills.file_service import (
    delete_skill_file,
    get_file_bytes,
    get_skill_files,
    read_text_content,
    set_skill_file,
)
from app.skills.inspector import parse_skill_md
from app.skills.package_hash import compute_package_tree_hash
from app.skills.package_metadata import sync_frontmatter
from app.skills.packager import PackageError, extract_package
from app.skills.runtime_dict import to_runtime_dict as to_runtime_dict
from app.storage.paths import (
    ensure_relative,
    resolve_company_data_path,
    resolve_data_path,
    skill_storage_dir,
)

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


# -- helpers -----------------------------------------------------------------


def slugify(value: str) -> str:
    """Lowercase, dash-separated, ASCII-only identifier."""

    base = value.strip().lower().replace("_", "-").replace(" ", "-")
    cleaned = _SLUG_RE.sub("-", base).strip("-")
    return cleaned or "skill"


# ADR-018 — ``skills.storage_path`` is stored relative to ``settings.data_root``
# as ``<dir>`` (package) or ``<dir>/SKILL.md`` (text), where ``<dir>`` is the
# company folder ``tenants/<tenant_id>/skills/<id>`` for new skills (I-08).


def _skill_root(skill_id: uuid.UUID, context: OrgContext) -> tuple[str, Path]:
    relative = skill_storage_dir(skill_id, context.tenant_id)
    return relative, resolve_data_path(relative)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- queries -----------------------------------------------------------------


async def list_skills(
    db: AsyncSession,
    context: OrgContext,
    *,
    kind: str | None = None,
    query: str | None = None,
) -> list[Skill]:
    await require_private_actor(db, context)
    stmt = private_scoped(select(Skill), Skill, context)
    if kind:
        stmt = stmt.where(Skill.kind == kind)
    if query:
        like = f"%{query.strip().lower()}%"
        stmt = stmt.where(func.lower(Skill.name).like(like))
    stmt = stmt.order_by(Skill.last_modified_at.desc(), Skill.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_skill(db: AsyncSession, skill_id: uuid.UUID, context: OrgContext) -> Skill | None:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    result = await db.execute(
        private_scoped(select(Skill).where(Skill.id == skill_id), Skill, context)
    )
    return result.scalar_one_or_none()


# -- mutations ---------------------------------------------------------------


async def create_text_skill(
    db: AsyncSession, creation: TextSkillCreation, context: OrgContext
) -> Skill:
    """Create a text skill, persisting ``content`` as ``SKILL.md`` on disk."""
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )

    name, slug, description, content, version = (
        creation.name,
        creation.slug,
        creation.description,
        creation.content,
        creation.version,
    )
    parse_skill_md(content, require_metadata=True)
    final_slug = slugify(slug or name)
    skill_id = uuid.uuid4()
    relative, root = _skill_root(skill_id, context)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    file_path = root / "SKILL.md"
    body_bytes = content.encode("utf-8")
    await asyncio.to_thread(file_path.write_bytes, body_bytes)

    skill = Skill(
        id=skill_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name=name,
        slug=final_slug,
        description=description,
        kind="text",
        storage_path=ensure_relative(f"{relative}/SKILL.md"),
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
    db: AsyncSession, creation: PackageSkillCreation, context: OrgContext
) -> Skill:
    """Extract a ``.skill`` zip and persist as a package skill."""
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )

    zip_bytes, name_override, slug_override = (
        creation.zip_bytes,
        creation.name_override,
        creation.slug_override,
    )
    skill_id = uuid.uuid4()
    relative, root = _skill_root(skill_id, context)
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
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name=final_name,
        slug=final_slug,
        description=info.description,
        kind="package",
        storage_path=relative,
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
    context: OrgContext,
    name: str | None = None,
    description: str | None = None,
    version: str | None = None,
) -> Skill:
    scoped = await get_skill(db, skill.id, context)
    if scoped is None:
        raise skill_not_found()
    skill = scoped
    if name is not None and name != skill.name:
        skill.name = name
    if description is not None:
        skill.description = description
    if version is not None:
        skill.version = version
    skill.is_dirty = True
    skill.last_modified_at = _now()
    await db.flush()
    return skill


async def update_text_content(
    db: AsyncSession, *, skill: Skill, content: str, context: OrgContext
) -> Skill:
    scoped = await get_skill(db, skill.id, context)
    if scoped is None:
        raise skill_not_found()
    skill = scoped
    if skill.kind != "text":
        raise ValueError("update_text_content only valid for text skills")
    if not skill.storage_path:
        raise ValueError("text skill missing storage_path")
    parse_skill_md(content, require_metadata=True)
    body_bytes = content.encode("utf-8")
    path = resolve_company_data_path(skill.storage_path, skill.tenant_id)  # I-08 fail closed
    await asyncio.to_thread(path.write_bytes, body_bytes)
    skill.content_hash = hashlib.sha256(body_bytes).hexdigest()
    skill.size_bytes = len(body_bytes)
    skill.is_dirty = True
    skill.last_modified_at = _now()
    sync_frontmatter(skill, body_bytes)
    await db.flush()
    return skill


async def delete_skill(db: AsyncSession, skill: Skill, context: OrgContext) -> None:
    """Remove the DB row and the on-disk storage.

    Both text and package skills live under ``<storage_dir>/<skill_id>/``;
    we wipe that directory regardless of kind. A missing path is a no-op.
    """

    scoped = await get_skill(db, skill.id, context)
    if scoped is None:
        raise skill_not_found()
    skill = scoped
    storage = skill.storage_path
    # I-08: refuse another company's path before the row goes (fail closed).
    path = resolve_company_data_path(storage, skill.tenant_id) if storage else None
    await db.delete(skill)
    await db.flush()
    if path is None:
        return
    # Text skills store the file at .../<id>/SKILL.md — delete the parent dir.
    is_file = await asyncio.to_thread(path.is_file)
    target = path.parent if is_file else path
    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)


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
