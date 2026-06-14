from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus, SkillDraftPackage
from app.services import skill_revision_service
from app.skills import service as skill_service
from app.skills.package_builder import build_skill_zip_bytes
from app.skills.validator import validate_draft_package


class SkillBuilderSourceSkillNotFound(LookupError):
    pass


class SkillBuilderValidationError(ValueError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("skill builder draft validation failed")
        self.result = result


async def create_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    user_request: str,
    mode: SkillBuilderMode = SkillBuilderMode.CREATE,
    source_skill_id: uuid.UUID | None = None,
) -> SkillBuilderSession:
    base_snapshot: dict[str, Any] | None = None
    base_skill_version: str | None = None
    base_content_hash: str | None = None
    if mode is SkillBuilderMode.IMPROVE:
        if source_skill_id is None:
            raise SkillBuilderSourceSkillNotFound("source skill is required")
        source_skill = await _get_owned_skill(db, source_skill_id, user_id)
        if source_skill is None:
            raise SkillBuilderSourceSkillNotFound("source skill not found")
        base_snapshot = await load_skill_snapshot(source_skill)
        base_skill_version = source_skill.version
        base_content_hash = source_skill.content_hash

    session = SkillBuilderSession(
        user_id=user_id,
        user_request=user_request,
        mode=mode.value,
        source_skill_id=source_skill_id if mode is SkillBuilderMode.IMPROVE else None,
        base_skill_version=base_skill_version,
        base_content_hash=base_content_hash,
        base_snapshot=base_snapshot,
        status=SkillBuilderStatus.COLLECTING.value,
    )
    db.add(session)
    await db.flush()
    return session


async def get_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SkillBuilderSession | None:
    result = await db.execute(
        select(SkillBuilderSession).where(
            SkillBuilderSession.id == session_id,
            SkillBuilderSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def append_message(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    role: str,
    content: str,
) -> SkillBuilderSession:
    messages = list(session.messages or [])
    messages.append(
        {
            "role": role,
            "content": content,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    session.messages = messages
    session.updated_at = _now()
    await db.flush()
    return session


async def save_draft_package(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    draft: dict[str, Any],
) -> SkillBuilderSession:
    session.draft_package = draft
    session.status = SkillBuilderStatus.REVIEW.value
    session.updated_at = _now()
    await db.flush()
    return session


async def save_validation_result(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    result: dict[str, Any],
) -> SkillBuilderSession:
    session.validation_result = result
    compatibility_result = result.get("compatibility_result")
    if isinstance(compatibility_result, dict):
        session.compatibility_result = compatibility_result
    session.updated_at = _now()
    await db.flush()
    return session


async def confirm_session(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    user_id: uuid.UUID,
) -> Skill:
    if session.mode != SkillBuilderMode.CREATE.value:
        raise NotImplementedError("improve confirm is not implemented")
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

    zip_bytes = build_skill_zip_bytes(slug=draft.slug, files=draft.files)
    skill = await skill_service.create_package_skill(db, user_id=user_id, zip_bytes=zip_bytes)
    skill.credential_requirements = list(draft.credential_requirements) or None
    skill.execution_profile = dict(draft.execution_profile) or None
    skill.source_kind = "user"
    skill.origin_kind = "created_by_me"
    session.validation_result = validation_result
    session.compatibility_result = validation_result.get("compatibility_result")
    changelog_summary = _changelog_summary(session.changelog_draft)
    await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="builder_create",
        source_session_id=session.id,
        compatibility_result=session.compatibility_result,
        changelog_summary=changelog_summary,
    )
    session.status = SkillBuilderStatus.COMPLETED.value
    session.finalized_skill_id = skill.id
    session.updated_at = _now()
    await db.flush()
    return skill


async def load_skill_snapshot(skill: Skill) -> dict[str, Any]:
    files: list[dict[str, Any]]
    if skill.kind == "text":
        content = await skill_service.read_text_content(skill)
        files = [{"path": "SKILL.md", "content": content, "role": "skill"}]
    else:
        files = []
        for file_info in skill_service.get_skill_files(skill):
            if file_info.is_dir:
                continue
            raw = skill_service.get_file_bytes(skill, file_info.path)
            files.append(
                {
                    "path": file_info.path,
                    "content": raw.decode("utf-8", errors="replace"),
                    "role": _role_for_path(file_info.path),
                }
            )
    return {
        "skill_id": str(skill.id),
        "kind": skill.kind,
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "version": skill.version,
        "content_hash": skill.content_hash,
        "credential_requirements": skill.credential_requirements or [],
        "execution_profile": skill.execution_profile or {},
        "files": files,
    }


async def _get_owned_skill(
    db: AsyncSession,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Skill | None:
    result = await db.execute(
        select(Skill).where(
            Skill.id == skill_id,
            Skill.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _role_for_path(path: str) -> str:
    if path == "SKILL.md":
        return "skill"
    if path.startswith("scripts/"):
        return "script"
    if path.startswith("references/"):
        return "reference"
    if path.startswith("assets/"):
        return "asset"
    if path.startswith("agents/"):
        return "metadata"
    if path.startswith("evals/"):
        return "eval"
    return "asset"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
