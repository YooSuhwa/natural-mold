from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.services.skill_builder_confirmation import confirm_builder_session
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.skills import service as skill_service


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
    return await confirm_builder_session(db, session, user_id=user_id)


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


__all__ = [
    "SkillBuilderConflictError",
    "SkillBuilderSourceSkillNotFound",
    "SkillBuilderValidationError",
    "append_message",
    "confirm_session",
    "create_session",
    "get_session",
    "load_skill_snapshot",
    "save_draft_package",
    "save_validation_result",
]
