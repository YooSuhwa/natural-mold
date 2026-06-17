from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.models.skill_revision import SkillRevision
from app.schemas.skill_builder import JsonValue, SkillBuilderMode
from app.services import skill_revision_audit


def confirm_audit_metadata(
    session: SkillBuilderSession,
    skill: Skill,
) -> dict[str, object]:
    draft_files = _path_content_map(session.draft_package)
    base_files = _path_content_map(session.base_snapshot)
    added = draft_files.keys() - base_files.keys()
    deleted = base_files.keys() - draft_files.keys()
    changed = {
        path
        for path in draft_files.keys() & base_files.keys()
        if draft_files[path] != base_files[path]
    }
    metadata: dict[str, object] = {
        "file_count": len(draft_files),
        "credential_requirement_count": len(skill.credential_requirements or []),
        "old_hash": session.base_content_hash,
        "new_hash": skill.content_hash,
    }
    if session.mode == SkillBuilderMode.IMPROVE.value:
        metadata.update(
            {
                "changed_file_count": len(changed),
                "added_file_count": len(added),
                "deleted_file_count": len(deleted),
            }
        )
    return metadata


def secret_scan_audit_metadata(
    validation_result: Mapping[str, object],
) -> dict[str, object] | None:
    issues = validation_result.get("issues")
    if not isinstance(issues, list):
        return None
    secret_issues = [
        issue
        for issue in issues
        if isinstance(issue, dict) and issue.get("code") == "SECRET_DETECTED"
    ]
    if not secret_issues:
        return None
    return {
        "finding_count": len(secret_issues),
        "finding_kinds": sorted(_text_values(secret_issues, "finding_kind")),
        "paths": sorted(_text_values(secret_issues, "path")),
    }


async def record_current_revision_create_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    skill: Skill,
) -> None:
    if skill.current_revision_id is None:
        return
    revision = await db.get(SkillRevision, skill.current_revision_id)
    if revision is None:
        return
    await skill_revision_audit.record_revision_create_audit(
        db,
        user=user,
        request=request,
        revision=revision,
    )


def _text_values(items: list[dict[object, object]], key: str) -> set[str]:
    values: set[str] = set()
    for item in items:
        value = item.get(key)
        if isinstance(value, str) and value:
            values.add(value)
    return values


def _path_content_map(raw: dict[str, JsonValue] | None) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    files = raw.get("files")
    if not isinstance(files, list):
        return {}
    result: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        content = item.get("content")
        if isinstance(path, str) and isinstance(content, str):
            result[path] = content
    return result
