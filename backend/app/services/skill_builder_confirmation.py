from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, assert_never

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus, SkillDraftPackage
from app.services import skill_revision_service
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.skills import service as skill_service
from app.skills.package_builder import build_skill_zip_bytes
from app.skills.package_hash import compute_package_tree_hash
from app.skills.packager import PackageInfo, extract_package
from app.skills.validator import validate_draft_package
from app.storage.paths import ensure_relative, resolve_data_path


@dataclass(frozen=True, slots=True)
class PackageReplacement:
    info: PackageInfo
    content_hash: str
    storage_path: str


async def confirm_builder_session(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    user_id: uuid.UUID,
) -> Skill:
    draft = _parse_draft(session.draft_package)
    validation_result = validate_draft_package(
        files=draft.files,
        credential_requirements=draft.credential_requirements,
        execution_profile=draft.execution_profile,
    )
    if validation_result["error_count"] > 0:
        session.validation_result = validation_result
        session.status = SkillBuilderStatus.REVIEW.value
        await db.flush()
        raise SkillBuilderValidationError(validation_result)

    session.validation_result = validation_result
    session.compatibility_result = validation_result.get("compatibility_result")
    mode = SkillBuilderMode(session.mode)
    match mode:
        case SkillBuilderMode.CREATE:
            skill = await _confirm_create(db, session=session, draft=draft, user_id=user_id)
        case SkillBuilderMode.IMPROVE:
            skill = await _confirm_improve(db, session=session, draft=draft, user_id=user_id)
        case unreachable:
            assert_never(unreachable)

    session.status = SkillBuilderStatus.COMPLETED.value
    session.finalized_skill_id = skill.id
    session.updated_at = skill.last_modified_at
    await db.flush()
    return skill


async def _confirm_create(
    db: AsyncSession,
    *,
    session: SkillBuilderSession,
    draft: SkillDraftPackage,
    user_id: uuid.UUID,
) -> Skill:
    zip_bytes = build_skill_zip_bytes(slug=draft.slug, files=draft.files)
    skill = await skill_service.create_package_skill(db, user_id=user_id, zip_bytes=zip_bytes)
    skill.credential_requirements = list(draft.credential_requirements) or None
    skill.execution_profile = dict(draft.execution_profile) or None
    skill.source_kind = "user"
    skill.origin_kind = "created_by_me"
    await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="builder_create",
        source_session_id=session.id,
        compatibility_result=session.compatibility_result,
        changelog_summary=_changelog_summary(session.changelog_draft),
    )
    return skill


async def _confirm_improve(
    db: AsyncSession,
    *,
    session: SkillBuilderSession,
    draft: SkillDraftPackage,
    user_id: uuid.UUID,
) -> Skill:
    skill = await _load_source_skill(db, session=session, user_id=user_id)
    if skill.content_hash != session.base_content_hash:
        session.status = SkillBuilderStatus.REVIEW.value
        session.error_message = "SOURCE_SKILL_CHANGED"
        await db.flush()
        raise SkillBuilderConflictError(
            base_content_hash=session.base_content_hash,
            current_content_hash=skill.content_hash,
        )

    parent_revision_id = skill.current_revision_id
    zip_bytes = build_skill_zip_bytes(slug=draft.slug, files=draft.files)
    replacement = await asyncio.to_thread(
        _replace_skill_storage,
        skill_id=skill.id,
        current_kind=skill.kind,
        current_storage_path=skill.storage_path,
        zip_bytes=zip_bytes,
    )
    skill.name = draft.name
    skill.slug = skill_service.slugify(draft.slug)
    skill.description = replacement.info.description or draft.description
    skill.kind = "package"
    skill.storage_path = replacement.storage_path
    skill.content_hash = replacement.content_hash
    skill.size_bytes = replacement.info.total_bytes
    skill.version = replacement.info.version
    skill.package_metadata = _package_metadata(replacement.info, skill.name)
    skill.credential_requirements = list(draft.credential_requirements) or None
    skill.execution_profile = dict(draft.execution_profile) or None
    skill.is_dirty = True
    skill.last_modified_at = _now()
    await db.flush()
    await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="builder_improvement",
        source_session_id=session.id,
        parent_revision_id=parent_revision_id,
        compatibility_result=session.compatibility_result,
        changelog_summary=_changelog_summary(session.changelog_draft),
    )
    return skill


async def _load_source_skill(
    db: AsyncSession,
    *,
    session: SkillBuilderSession,
    user_id: uuid.UUID,
) -> Skill:
    if session.source_skill_id is None:
        raise SkillBuilderSourceSkillNotFound("source skill is required")
    skill = await skill_service.get_skill(db, session.source_skill_id, user_id)
    if skill is None:
        raise SkillBuilderSourceSkillNotFound("source skill not found")
    return skill


def _parse_draft(raw: dict[str, Any] | None) -> SkillDraftPackage:
    if raw is None:
        raise SkillBuilderValidationError(
            _draft_error_result("DRAFT_PACKAGE_MISSING", "Draft package is required.")
        )
    try:
        return SkillDraftPackage.model_validate(raw)
    except ValidationError as exc:
        raise SkillBuilderValidationError(
            _draft_error_result("DRAFT_PACKAGE_INVALID", str(exc))
        ) from exc


def _draft_error_result(code: str, message: str) -> dict[str, Any]:
    return {
        "valid": False,
        "error_count": 1,
        "warning_count": 0,
        "info_count": 0,
        "issues": [{"code": code, "severity": "error", "path": None, "message": message}],
    }


def _changelog_summary(changelog: dict[str, Any] | None) -> str | None:
    if not isinstance(changelog, dict):
        return None
    summary = changelog.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary
    return None


def _replace_skill_storage(
    *,
    skill_id: uuid.UUID,
    current_kind: str,
    current_storage_path: str | None,
    zip_bytes: bytes,
) -> PackageReplacement:
    storage_path = ensure_relative(f"skills/{skill_id}")
    root = _skill_root(skill_id, current_kind, current_storage_path)
    with TemporaryDirectory() as temp_dir:
        extracted = Path(temp_dir) / "skill"
        info = extract_package(zip_bytes, extracted)
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(extracted, root)
    return PackageReplacement(
        info=info,
        content_hash=compute_package_tree_hash(root),
        storage_path=storage_path,
    )


def _skill_root(
    skill_id: uuid.UUID,
    current_kind: str,
    current_storage_path: str | None,
) -> Path:
    if current_storage_path is None:
        return resolve_data_path(f"skills/{skill_id}")
    current_path = resolve_data_path(current_storage_path)
    if current_kind == "text":
        return current_path.parent
    return current_path


def _package_metadata(info: PackageInfo, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": info.version,
        "files": info.files,
        "has_scripts": info.has_scripts,
        "frontmatter": info.metadata,
    }


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
