from __future__ import annotations

import shutil
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.models.skill_builder_session import JsonValue
from app.models.skill_revision import SkillRevision
from app.services.skill_locks import lock_skill_for_mutation
from app.services.skill_revision_storage import write_skill_revision_snapshot
from app.skills import service as skill_service
from app.skills.moldy_metadata import (
    credential_requirements_from_metadata,
    execution_profile_from_metadata,
    parse_moldy_metadata_content,
)
from app.skills.package_metadata import refresh_package_metadata, sync_frontmatter
from app.skills.packager import extract_package
from app.storage.paths import resolve_data_path


async def create_revision_for_skill(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    operation: str,
    source_session_id: uuid.UUID | None = None,
    parent_revision_id: uuid.UUID | None = None,
    restored_from_revision_id: uuid.UUID | None = None,
    changed_files: list[JsonValue] | None = None,
    changelog_summary: str | None = None,
    changelog_items: list[JsonValue] | None = None,
    compatibility_result: dict[str, JsonValue] | None = None,
    evaluation_summary: dict[str, JsonValue] | None = None,
    metadata_json: dict[str, JsonValue] | None = None,
) -> SkillRevision:
    revision_number = await _next_revision_number(db, skill.id)
    snapshot = await write_skill_revision_snapshot(skill, revision_number=revision_number)
    revision = SkillRevision(
        skill_id=skill.id,
        user_id=user_id,
        source_session_id=source_session_id,
        parent_revision_id=parent_revision_id,
        restored_from_revision_id=restored_from_revision_id,
        revision_number=revision_number,
        operation=operation,
        skill_version=skill.version,
        content_hash=skill.content_hash,
        storage_provider=snapshot.storage_provider,
        object_key=snapshot.object_key,
        size_bytes=snapshot.size_bytes,
        file_count=snapshot.file_count,
        changed_files=changed_files,
        changelog_summary=changelog_summary,
        changelog_items=changelog_items,
        compatibility_result=compatibility_result,
        evaluation_summary=evaluation_summary,
        metadata_json=metadata_json or {},
    )
    db.add(revision)
    await db.flush()
    skill.current_revision_id = revision.id
    await db.flush()
    return revision


async def list_revisions(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> list[SkillRevision]:
    if skill.user_id != user_id:
        return []
    result = await db.execute(
        select(SkillRevision)
        .where(SkillRevision.skill_id == skill.id, SkillRevision.user_id == user_id)
        .order_by(desc(SkillRevision.revision_number))
    )
    return list(result.scalars().all())


async def get_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    revision_id: uuid.UUID,
) -> SkillRevision | None:
    if skill.user_id != user_id:
        return None
    result = await db.execute(
        select(SkillRevision).where(
            SkillRevision.id == revision_id,
            SkillRevision.skill_id == skill.id,
            SkillRevision.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def rollback_to_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    revision: SkillRevision,
    changelog_summary: str | None = None,
) -> SkillRevision:
    if skill.user_id != user_id or revision.skill_id != skill.id:
        raise SkillRevisionNotFound("revision not found")
    if snapshot_pruned(revision):
        raise SkillRevisionRollbackUnsupported("revision snapshot was pruned")
    skill = await lock_skill_for_mutation(db, skill=skill)
    parent_revision_id = skill.current_revision_id
    zip_bytes = await anyio.to_thread.run_sync(_read_revision_bytes, revision.object_key)
    if skill.kind == "text":
        content = await anyio.to_thread.run_sync(_read_skill_md, zip_bytes)
        await skill_service.update_text_content(db, skill=skill, content=content)
    else:
        if not skill.storage_path:
            raise SkillRevisionRollbackUnsupported("package skill has no storage path")
        await anyio.to_thread.run_sync(_replace_package_files, skill.storage_path, zip_bytes)
        refresh_package_metadata(skill)
        sync_frontmatter(skill, skill_service.get_file_bytes(skill, "SKILL.md"))
        _sync_moldy_runtime_columns(skill)
        await db.flush()
    return await create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="rollback",
        parent_revision_id=parent_revision_id,
        restored_from_revision_id=revision.id,
        changelog_summary=changelog_summary,
    )


async def _next_revision_number(db: AsyncSession, skill_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.max(SkillRevision.revision_number)).where(SkillRevision.skill_id == skill_id)
    )
    current = result.scalar_one_or_none()
    if current is None:
        return 1
    return int(current) + 1


def snapshot_pruned(revision: SkillRevision) -> bool:
    return bool((revision.metadata_json or {}).get("snapshot_pruned"))


# 스냅샷 zip에서 파일을 읽는 표시-계층 상한 — 드래프트 워크스페이스 어댑터의
# 2MB/8KB 계약(skill_draft_workspace)과 동일한 fail-closed 방향.
_REVISION_FILE_SNIFF_BYTES = 8192
_MAX_REVISION_FILE_BYTES = 2 * 1024 * 1024


async def list_revision_files(revision: SkillRevision) -> list[tuple[str, int, bool]]:
    """리비전 스냅샷 zip의 파일 목록 — (path, size, is_binary).

    디스크 추출 없이 central directory + head sniff만 읽는다(zip-slip 표면
    없음). pruned 스냅샷은 호출 전에 ``snapshot_pruned``로 걸러야 한다.
    """

    return await anyio.to_thread.run_sync(_list_revision_files_sync, revision.object_key)


async def load_revision_file_content(revision: SkillRevision, relative_path: str) -> str | None:
    """리비전 스냅샷의 단일 파일 텍스트 — 열거 경로와 **정확 일치**할 때만.

    traversal은 매칭 실패(None→404)로 끝난다. 바이너리(널바이트)·2MB 초과는
    None — 표시 계층 fail-closed(드래프트 레일 뷰어와 동일 계약).
    """

    return await anyio.to_thread.run_sync(
        _load_revision_file_content_sync, revision.object_key, relative_path
    )


def _list_revision_files_sync(object_key: str) -> list[tuple[str, int, bool]]:
    zip_bytes = _read_revision_bytes(object_key)
    entries: list[tuple[str, int, bool]] = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            with archive.open(info) as handle:
                sniff = handle.read(_REVISION_FILE_SNIFF_BYTES)
            entries.append((info.filename, info.file_size, b"\x00" in sniff))
    entries.sort(key=lambda entry: entry[0])
    return entries


def _load_revision_file_content_sync(object_key: str, relative_path: str) -> str | None:
    zip_bytes = _read_revision_bytes(object_key)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename != relative_path:
                continue
            # 헤더의 file_size를 믿지 않고 스트림을 상한+1까지 읽어 검증한다.
            with archive.open(info) as handle:
                raw = handle.read(_MAX_REVISION_FILE_BYTES + 1)
            if len(raw) > _MAX_REVISION_FILE_BYTES or b"\x00" in raw:
                return None
            return raw.decode("utf-8", errors="replace")
    return None


def _sync_moldy_runtime_columns(skill: Skill) -> None:
    try:
        raw = skill_service.get_file_bytes(skill, "agents/moldy.yaml").decode("utf-8")
    except FileNotFoundError:
        metadata: dict[str, JsonValue] = {}
    else:
        parsed, _issues = parse_moldy_metadata_content(raw)
        metadata = parsed
    requirements = credential_requirements_from_metadata(metadata)
    profile = execution_profile_from_metadata(metadata)
    skill.credential_requirements = [dict(item) for item in requirements] or None
    skill.execution_profile = dict(profile) or None


class SkillRevisionNotFound(LookupError):
    pass


class SkillRevisionRollbackUnsupported(RuntimeError):
    pass


def _read_revision_bytes(object_key: str) -> bytes:
    path = (Path(settings.data_root) / object_key).resolve()
    root = Path(settings.data_root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("skill revision path escapes data root")
    return path.read_bytes()


def _read_skill_md(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        return archive.read("SKILL.md").decode("utf-8")


def _replace_package_files(storage_path: str, zip_bytes: bytes) -> None:
    root = resolve_data_path(storage_path)
    with TemporaryDirectory() as temp_dir:
        extracted = Path(temp_dir) / "skill"
        extract_package(zip_bytes, extracted)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(extracted, root)
