from __future__ import annotations

import shutil
import uuid
import zipfile
import zlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import yaml
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.models.skill_builder_session import JsonValue
from app.models.skill_revision import SkillRevision
from app.services.skill_locks import lock_skill_for_mutation
from app.services.skill_revision_storage import write_skill_revision_snapshot
from app.skills import service as skill_service
from app.skills.display_limits import DISPLAY_TEXT_SNIFF_BYTES, MAX_DISPLAY_TEXT_BYTES
from app.skills.inspector import parse_skill_md
from app.skills.moldy_metadata import (
    credential_requirements_from_metadata,
    execution_profile_from_metadata,
    parse_moldy_metadata_content,
)
from app.skills.package_metadata import refresh_package_metadata, sync_frontmatter
from app.skills.packager import PackageError, extract_package
from app.storage.paths import resolve_data_path

# 리비전 파일 path 상한 — content 엔드포인트 Query 바운드와 목록 필터가 공유한다
# (zip 엔트리 이름은 65,535바이트까지 가능; 비대칭이면 목록엔 있는데 못 여는
# 파일이 생긴다, R6).
MAX_REVISION_FILE_PATH_CHARS = 4096


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
    limit: int | None = 100,
) -> list[SkillRevision]:
    """리비전 목록 (최신순).

    ``limit=None``은 전수 열거 — retention prune처럼 의미상 무제한 순회가
    필요한 소비자용이다. 기본 100 창만 보면 창 밖(>100번째) 리비전이 영구히
    prune 대상에서 빠져 스냅샷 디스크가 새는 잠복 계약 파손이 된다 (R5).
    """
    if skill.user_id != user_id:
        return []
    stmt = (
        select(SkillRevision)
        .where(SkillRevision.skill_id == skill.id, SkillRevision.user_id == user_id)
        .order_by(desc(SkillRevision.revision_number))
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
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
    try:
        zip_bytes = await anyio.to_thread.run_sync(_read_revision_bytes, revision.object_key)
    except FileNotFoundError as exc:
        raise SkillRevisionSnapshotMissing("revision snapshot file is missing") from exc
    if skill.kind == "text":
        try:
            content = await anyio.to_thread.run_sync(_read_skill_md, zip_bytes)
        except (zipfile.BadZipFile, zlib.error, KeyError, UnicodeDecodeError) as exc:
            # UnicodeDecodeError: CRC는 멀쩡한데 비 UTF-8 바이트인 스냅샷 —
            # decode는 zip read가 아니라 여기서 터진다 (R7).
            raise SkillRevisionSnapshotMissing("revision snapshot is unreadable") from exc
        try:
            # update_text_content도 write 전에 같은 파싱을 돌리지만, 여기서
            # 선검증해야 "스냅샷 불가" 부류(레거시 frontmatter 등)가 형제
            # 케이스(유실/손상)와 같은 409로 수렴한다 — 500 비대칭 방지 (R6).
            # yaml.YAMLError: frontmatter.loads의 파서 오류는 ValueError 계열이
            # 아니다(SkillMetadataError만 ValueError) — 깨진 YAML frontmatter가
            # frontmatter 부재와 다른 응답이 되면 안 된다 (R7).
            parse_skill_md(content, require_metadata=True)
        except (ValueError, yaml.YAMLError) as exc:
            raise SkillRevisionSnapshotMissing("revision snapshot SKILL.md is invalid") from exc
        await skill_service.update_text_content(db, skill=skill, content=content)
    else:
        if not skill.storage_path:
            raise SkillRevisionRollbackUnsupported("package skill has no storage path")
        # validate-then-mutate: 파괴적 교체(rmtree) 전에 zip 무결성과 SKILL.md
        # 존재를 검증한다. 교체 후 실패하면 디스크는 이미 바뀌었는데 DB만
        # 롤백되는 발산이 남으므로, "스냅샷 불가" 부류는 전부 여기서 걸러
        # 무변경 409로 끝낸다 (R5).
        await anyio.to_thread.run_sync(_validate_package_snapshot, zip_bytes)
        await anyio.to_thread.run_sync(_replace_package_files, skill.storage_path, zip_bytes)
        refresh_package_metadata(skill)
        sync_frontmatter(skill, skill_service.get_file_bytes(skill, "SKILL.md"))
        _sync_moldy_runtime_columns(skill)
        # 형제 패키지 변이(file_service.set_skill_file)와 동일하게 수정 시각을
        # 갱신 — 목록 정렬(last_modified_at desc)·UI 타임스탬프 정합 (R5).
        # naive UTC — 컬럼/형제 _now()와 동일 규약.
        skill.last_modified_at = datetime.now(UTC).replace(tzinfo=None)
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


async def list_revision_files(revision: SkillRevision) -> list[tuple[str, int, bool]] | None:
    """리비전 스냅샷 zip의 파일 목록 — (path, size, is_binary).

    디스크 추출 없이 central directory + head sniff만 랜덤 액세스로 읽는다
    (전체 bytes 적재 없음, zip-slip 표면 없음). pruned는 호출 전에
    ``snapshot_pruned``로 거르고, **zip이 디스크에 없으면 None**을 반환한다
    (pruned 플래그 없이 파일만 유실된 스냅샷 — 라우터가 pruned와 동일하게
    처리해 500 대신 명시 응답을 낸다).
    """

    return await anyio.to_thread.run_sync(_list_revision_files_sync, revision.object_key)


async def load_revision_file_content(revision: SkillRevision, relative_path: str) -> str | None:
    """리비전 스냅샷의 단일 파일 텍스트 — 열거 경로와 **정확 일치**할 때만.

    traversal은 매칭 실패(None→404)로 끝난다. 바이너리(널바이트)·상한 초과·
    스냅샷 유실도 None — 표시 계층 fail-closed(드래프트 레일 뷰어와 동일,
    상한 정본은 app.skills.display_limits).
    """

    return await anyio.to_thread.run_sync(
        _load_revision_file_content_sync, revision.object_key, relative_path
    )


def _list_revision_files_sync(object_key: str) -> list[tuple[str, int, bool]] | None:
    try:
        archive = zipfile.ZipFile(_revision_snapshot_path(object_key))
    except (FileNotFoundError, zipfile.BadZipFile):
        # 유실뿐 아니라 손상(중단된 쓰기 등)도 pruned와 동일 계약 — 500 금지 (R5).
        return None
    entries: list[tuple[str, int, bool]] = []
    try:
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                # content 엔드포인트 path 상한과 대칭 — 목록에는 있는데 열 수 없는
                # 엔트리를 만들지 않는다 (R6).
                if len(info.filename) > MAX_REVISION_FILE_PATH_CHARS:
                    continue
                with archive.open(info) as handle:
                    sniff = handle.read(DISPLAY_TEXT_SNIFF_BYTES)
                entries.append((info.filename, info.file_size, b"\x00" in sniff))
    except (zipfile.BadZipFile, zlib.error):
        # central directory는 멀쩡한데 멤버 바이트가 손상(Bad CRC 등) — open 시점
        # 검사를 통과한 손상도 같은 계약으로 (R6).
        return None
    entries.sort(key=lambda entry: entry[0])
    return entries


def _load_revision_file_content_sync(object_key: str, relative_path: str) -> str | None:
    try:
        archive = zipfile.ZipFile(_revision_snapshot_path(object_key))
    except (FileNotFoundError, zipfile.BadZipFile):
        return None
    with archive:
        try:
            info = archive.getinfo(relative_path)
        except KeyError:
            return None
        if info.is_dir():
            return None
        # 헤더의 file_size를 믿지 않고 스트림을 상한+1까지 읽어 검증한다.
        # 멤버 바이트 손상(Bad CRC/zlib)은 open이 아니라 read에서 터진다 (R6).
        try:
            with archive.open(info) as handle:
                raw = handle.read(MAX_DISPLAY_TEXT_BYTES + 1)
        except (zipfile.BadZipFile, zlib.error):
            return None
        if len(raw) > MAX_DISPLAY_TEXT_BYTES or b"\x00" in raw:
            return None
        return raw.decode("utf-8", errors="replace")


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


class SkillRevisionSnapshotMissing(RuntimeError):
    """스냅샷 유실/손상 — 디스크 **무변경** 상태에서만 발생해야 한다.

    라우터는 이 예외(+RollbackUnsupported)만 409로 매핑한다. 변이 이후의
    FileNotFoundError를 409로 뭉개면 "아무 일 없었음" 응답 뒤에 부분 변이가
    숨는다 (R5).
    """


def _revision_snapshot_path(object_key: str) -> Path:
    path = (Path(settings.data_root) / object_key).resolve()
    root = Path(settings.data_root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("skill revision path escapes data root")
    return path


def _read_revision_bytes(object_key: str) -> bytes:
    return _revision_snapshot_path(object_key).read_bytes()


def _read_skill_md(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        return archive.read("SKILL.md").decode("utf-8")


def _validate_package_snapshot(zip_bytes: bytes) -> None:
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            names = set(archive.namelist())
            # central directory가 멀쩡해도 멤버 바이트가 손상(bitrot/부분 쓰기)일
            # 수 있다 — testzip으로 CRC 전수 검사해야 extract 단계 500을 막는다 (R6).
            corrupt_member = archive.testzip()
            skill_md = archive.read("SKILL.md") if "SKILL.md" in names else None
    except (zipfile.BadZipFile, zlib.error) as exc:
        raise SkillRevisionSnapshotMissing("revision snapshot zip is corrupt") from exc
    if corrupt_member is not None:
        raise SkillRevisionSnapshotMissing(
            f"revision snapshot member is corrupt: {corrupt_member!r}"
        )
    if skill_md is None:
        raise SkillRevisionSnapshotMissing("revision snapshot is missing SKILL.md")
    try:
        # 파싱 가능성까지 변이 전에 검증 — extract_package 내부 파싱의 YAML
        # 파서 오류는 PackageError가 아니라서(SkillMetadataError만 래핑) 여기서
        # 걸러야 frontmatter 부재 형제와 같은 409로 수렴한다 (R7).
        parse_skill_md(skill_md, require_metadata=True)
    except (ValueError, yaml.YAMLError) as exc:
        raise SkillRevisionSnapshotMissing("revision snapshot SKILL.md is invalid") from exc


def _replace_package_files(storage_path: str, zip_bytes: bytes) -> None:
    root = resolve_data_path(storage_path)
    with TemporaryDirectory() as temp_dir:
        extracted = Path(temp_dir) / "skill"
        try:
            # 추출은 tempdir에서 rmtree **이전** — 여기서의 거부(zip-slip/symlink/
            # 널바이트, PackageError)는 무변이이므로 409 계약으로 수렴시킨다 (R6).
            # yaml.YAMLError: extract 내부 SKILL.md 파싱은 SkillMetadataError만
            # PackageError로 래핑한다 — validate가 선검증하지만 belt-and-braces (R7).
            extract_package(zip_bytes, extracted)
        except (PackageError, yaml.YAMLError) as exc:
            raise SkillRevisionSnapshotMissing("revision snapshot package is invalid") from exc
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(extracted, root)
