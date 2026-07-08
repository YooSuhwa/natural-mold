"""스킬 드래프트 워크스페이스 파일시스템 서비스 (스펙 AD-2).

세션마다 ``data/skill-drafts/<session_id>/`` 물리 디렉토리를 만들고, 런타임은
이를 가상 경로 ``/skill-drafts/<session_id>/``로 마운트한다. 경로는 전부
ADR-018 상대경로 계약(``storage/paths``)을 따른다.

책임: 생성 / 시드(improve 원본 복사) / 첨부→``inputs/`` 복사 /
디렉토리→``SkillDraftFile`` 어댑터 / GC(세션 상태 기준).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillDraftFile
from app.storage.paths import ensure_relative, resolve_data_path

if TYPE_CHECKING:
    from app.models.message_attachment import MessageAttachment
    from app.models.skill import Skill
    from app.schemas.skill_builder import SkillDraftPackage

logger = logging.getLogger(__name__)

SKILL_DRAFTS_ROOT = "skill-drafts"

# 사용자 시험 입력 전용 디렉토리 — 스킬 패키지 콘텐츠가 아니므로 어댑터
# (검증/zip 수거)에서 제외한다.
INPUTS_DIR = "inputs"

# GC가 삭제할 수 있는 세션 상태 (스펙 AD-2): active/confirming은 보존.
GC_DELETABLE_STATUSES = ("completed", "abandoned")

# 어댑터가 파일 하나에서 읽는 최대 바이트 — 검증 입력 폭주 방어.
_MAX_ADAPTER_FILE_BYTES = 2 * 1024 * 1024


def workspace_storage_path(session_id: uuid.UUID) -> str:
    """세션 워크스페이스의 data_root 기준 상대 경로 (ADR-018)."""

    return ensure_relative(f"{SKILL_DRAFTS_ROOT}/{session_id}")


def resolve_workspace_dir(storage_path: str) -> Path:
    """``draft_workspace_path`` 컬럼 값 → 절대 경로."""

    return resolve_data_path(storage_path)


def create_workspace(session_id: uuid.UUID) -> str:
    """워크스페이스 디렉토리를 만들고 상대 storage path를 반환한다 (멱등)."""

    storage_path = workspace_storage_path(session_id)
    resolve_data_path(storage_path).mkdir(parents=True, exist_ok=True)
    return storage_path


def seed_workspace_from_skill(skill: Skill, session_id: uuid.UUID) -> str:
    """improve 모드 시드 — 원본 스킬 파일을 워크스페이스로 **복사**한다.

    스킬 마운트의 materialize 선례를 따른다(``skill_runtime._materialize_skill``):
    text-kind는 단일 ``SKILL.md`` 파일, package-kind는 ``copytree``.
    ``symlinks=False`` — 드래프트 편집이 공유 원본으로 역류하면 안 된다.
    소스가 디스크에 없으면 경고 후 빈 워크스페이스로 시작한다(대화에서
    에이전트가 base_snapshot으로 상황을 설명할 수 있도록 실패시키지 않음).
    """

    storage_path = workspace_storage_path(session_id)
    target = resolve_data_path(storage_path)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)

    src = resolve_data_path(skill.storage_path)
    if not src.exists():
        logger.warning(
            "skill draft seed skipped — source missing: skill=%s path=%s",
            skill.slug,
            src,
        )
        target.mkdir(parents=True, exist_ok=True)
        return storage_path
    if src.is_file():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target / "SKILL.md")
    else:
        shutil.copytree(src, target, symlinks=False)
    return storage_path


def copy_attachments_to_inputs(
    storage_path: str,
    attachments: Sequence[MessageAttachment],
) -> list[str]:
    """대화 첨부 blob을 ``<workspace>/inputs/``로 **복사**한다 (마운트 금지, §6-3).

    반환: 복사된 상대 경로(``inputs/<이름>``) 목록. 파일명은 사용자 입력이라
    경로 성분을 제거해 traversal을 차단하고, 충돌 시 순번을 붙인다. blob이
    없으면 건너뛴다 (orphan GC와의 경합 허용).
    """

    inputs_dir = resolve_workspace_dir(storage_path) / INPUTS_DIR
    inputs_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for attachment in attachments:
        src = resolve_data_path(attachment.storage_path)
        if not src.is_file():
            logger.warning(
                "attachment copy skipped — blob missing: attachment=%s path=%s",
                attachment.id,
                src,
            )
            continue
        # 파일명 새니타이즈: 경로 성분 제거(traversal 차단) + 빈 이름 방어.
        safe_name = Path(attachment.filename).name or f"attachment-{attachment.id}"
        destination = inputs_dir / safe_name
        counter = 1
        while destination.exists():
            destination = inputs_dir / f"{Path(safe_name).stem}-{counter}{Path(safe_name).suffix}"
            counter += 1
        shutil.copyfile(src, destination)
        copied.append(f"{INPUTS_DIR}/{destination.name}")
    return copied


def load_draft_files(storage_path: str) -> list[SkillDraftFile]:
    """워크스페이스 디렉토리 → ``SkillDraftFile`` 리스트 어댑터 (text-only).

    validate/finalize의 입력 계약(스펙 AD-3). ``inputs/``(시험 입력)는 패키지
    콘텐츠가 아니라 제외. 바이너리(널 바이트 포함)는 skip하고, 그 외에는
    ``errors="replace"``로 디코드한다 (스냅샷 로더 선례).
    """

    root = resolve_workspace_dir(storage_path)
    if not root.is_dir():
        return []
    files: list[SkillDraftFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] == INPUTS_DIR:
            continue
        raw = path.read_bytes()[:_MAX_ADAPTER_FILE_BYTES]
        if b"\x00" in raw:
            logger.debug("draft adapter skipped binary file: %s", relative)
            continue
        files.append(
            SkillDraftFile(
                path=relative,
                content=raw.decode("utf-8", errors="replace"),
                role=role_for_path(relative),
            )
        )
    return files


_BINARY_SNIFF_BYTES = 8192


def _iter_draft_paths(storage_path: str):
    """어댑터와 동일한 필터(inputs/ 제외·symlink 제외·바이너리 sniff skip)로
    (relative_path, disk_path)를 순회한다 — **내용을 전부 읽지 않는다**.

    파일 목록/단건 조회 API가 워크스페이스 전체 바이트를 매 요청 적재하지
    않도록(R2 perf) 바이너리 판정은 앞 8KB sniff로 제한한다. 전량 판정이
    필요한 validate/finalize 경로는 기존 ``load_draft_files``를 그대로 쓴다.
    """

    root = resolve_workspace_dir(storage_path)
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] == INPUTS_DIR:
            continue
        try:
            with path.open("rb") as handle:
                sniff = handle.read(_BINARY_SNIFF_BYTES)
        except OSError:
            continue
        if b"\x00" in sniff:
            continue
        yield relative, path


def list_draft_file_entries(storage_path: str) -> list[tuple[str, int, str]]:
    """파일 목록 메타데이터 — (path, size, role). ``st_size`` 기반(내용 미독)."""

    entries: list[tuple[str, int, str]] = []
    for relative, path in _iter_draft_paths(storage_path):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append((relative, min(size, _MAX_ADAPTER_FILE_BYTES), role_for_path(relative)))
    return entries


def load_draft_file_content(storage_path: str, relative_path: str) -> SkillDraftFile | None:
    """단일 파일 내용 — 요청 경로가 열거 경로와 **정확 일치**할 때만 그 파일만
    읽는다 (traversal은 매칭 실패 = None, 어댑터 계약과 동일)."""

    for relative, path in _iter_draft_paths(storage_path):
        if relative != relative_path:
            continue
        raw = path.read_bytes()[:_MAX_ADAPTER_FILE_BYTES]
        if b"\x00" in raw:
            return None
        return SkillDraftFile(
            path=relative,
            content=raw.decode("utf-8", errors="replace"),
            role=role_for_path(relative),
        )
    return None


DRAFT_FALLBACK_SLUG = "draft"
_SAFE_SLUG_RE_STR = r"[a-z0-9][a-z0-9_-]{0,63}"


def draft_slug(files: Sequence[SkillDraftFile]) -> str:
    """SKILL.md 프론트매터 ``name`` → 새니타이즈된 slug (실패 시 'draft').

    LLM 저작 값이라 엄격히 새니타이즈한다 — 샌드박스 materialize가
    ``runtime_root / slug``로 복사하므로 경로 성분이 섞이면 traversal.
    """

    import re

    skill_md = next((f for f in files if f.path == "SKILL.md"), None)
    if skill_md is None:
        return DRAFT_FALLBACK_SLUG
    from app.skills.inspector import SkillMetadataError, parse_skill_md

    try:
        parsed = parse_skill_md(skill_md.content, require_metadata=True)
    except SkillMetadataError:
        return DRAFT_FALLBACK_SLUG
    raw = str(parsed["metadata"].get("name") or "").strip().lower()
    match = re.fullmatch(_SAFE_SLUG_RE_STR, raw)
    return match.group(0) if match else DRAFT_FALLBACK_SLUG


def build_draft_package(storage_path: str) -> SkillDraftPackage:
    """워크스페이스 디렉토리 → ``SkillDraftPackage`` (finalize 입력, M5).

    name/description은 SKILL.md 프론트매터, credential_requirements/
    execution_profile은 ``agents/moldy.yaml``에서 파생한다. 파싱 실패 시
    자리표시자를 채워 confirm의 패키지 검증이 정확한 이슈를 보고하게 한다.
    """

    from app.schemas.skill_builder import SkillDraftPackage
    from app.skills.inspector import SkillMetadataError, parse_skill_md
    from app.skills.moldy_metadata import (
        credential_requirements_from_metadata,
        execution_profile_from_metadata,
        load_moldy_metadata,
    )

    files = load_draft_files(storage_path)
    name = "Draft Skill"
    description = "(missing SKILL.md description)"
    skill_md = next((f for f in files if f.path == "SKILL.md"), None)
    if skill_md is not None:
        try:
            parsed = parse_skill_md(skill_md.content, require_metadata=True)
            metadata = parsed["metadata"]
            name = str(metadata.get("name") or name)
            description = str(metadata.get("description") or description)
        except SkillMetadataError:
            pass

    moldy_metadata, _issues = load_moldy_metadata({f.path: f for f in files})
    return SkillDraftPackage(
        name=name[:160],
        slug=draft_slug(files),
        description=description[:1000],
        files=files,
        credential_requirements=[
            dict(item) for item in credential_requirements_from_metadata(moldy_metadata)
        ],
        execution_profile=dict(execution_profile_from_metadata(moldy_metadata)),
    )


def binary_package_files(storage_path: str) -> list[str]:
    """패키지 콘텐츠(비-``inputs/``) 중 바이너리 파일 경로 목록.

    finalize는 text-only 어댑터를 zip 소스로 쓰므로 바이너리는 조용히
    누락된다 — improve 시드 원본에 바이너리 asset이 있으면 fail-closed로
    안내하기 위한 탐지 (Phase 1.5: 디스크 기반 zip으로 해제).
    """

    root = resolve_workspace_dir(storage_path)
    if not root.is_dir():
        return []
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] == INPUTS_DIR:
            continue
        if b"\x00" in path.read_bytes()[:_MAX_ADAPTER_FILE_BYTES]:
            found.append(relative)
    return found


def draft_execution_profile(storage_path: str) -> dict[str, object]:
    """드래프트의 execution_profile (``agents/moldy.yaml`` 기준, 없으면 {})."""

    files = load_draft_files(storage_path)
    by_path = {f.path: f for f in files}
    from app.skills.moldy_metadata import (
        execution_profile_from_metadata,
        load_moldy_metadata,
    )

    metadata, _issues = load_moldy_metadata(by_path)
    return dict(execution_profile_from_metadata(metadata))


def draft_requires_network(storage_path: str) -> bool:
    """세션 동의 가능성 게이트 (AD-4 경계) — 현재 드래프트 상태를 매번 재평가.

    동의가 기록된 뒤 드래프트가 ``requires_network: true``로 바뀔 수 있으므로,
    동의 기록 시점과 정책 적용 시점 **양쪽**에서 이 함수를 확인해야 한다.
    """

    return bool(draft_execution_profile(storage_path).get("requires_network"))


async def copy_conversation_attachments_to_inputs(
    db: AsyncSession,
    *,
    storage_path: str,
    attachment_ids: Sequence[uuid.UUID],
    user_id: uuid.UUID,
) -> list[str]:
    """run 시작 시 방금 링크된 첨부를 ``inputs/``로 복사한다 (소유권 필터 포함)."""

    if not attachment_ids:
        return []
    from app.models.message_attachment import MessageAttachment

    result = await db.execute(
        select(MessageAttachment).where(
            MessageAttachment.id.in_(list(attachment_ids)),
            MessageAttachment.user_id == user_id,
        )
    )
    return copy_attachments_to_inputs(storage_path, list(result.scalars()))


def build_skill_draft_brief(session: SkillBuilderSession) -> dict[str, object]:
    """``moldy.skill_draft`` stream-head 페이로드 (AD-5).

    요약만 싣는다 — 세션 id/모드/slug/파일 경로·크기/base 대비 변경 수.
    파일 **내용**은 절대 싣지 않는다 (§6-7; 내용은 도구 결과/FS 읽기로만).
    """

    files = load_draft_files(session.draft_workspace_path) if session.draft_workspace_path else []
    base_files: dict[str, str] = {}
    base_snapshot = session.base_snapshot or {}
    for raw in base_snapshot.get("files") or []:
        if isinstance(raw, dict) and isinstance(raw.get("path"), str):
            base_files[raw["path"]] = str(raw.get("content") or "")

    current_paths = {f.path for f in files}
    changed = sum(1 for f in files if f.path not in base_files or base_files[f.path] != f.content)
    deleted = len(set(base_files) - current_paths)

    slug: str | None = None
    skill_md = next((f for f in files if f.path == "SKILL.md"), None)
    if skill_md is not None:
        from app.skills.inspector import SkillMetadataError, parse_skill_md

        try:
            parsed = parse_skill_md(skill_md.content, require_metadata=True)
            raw_slug = parsed["metadata"].get("name")
            slug = str(raw_slug) if raw_slug else None
        except SkillMetadataError:
            slug = None

    # 검증 레일 상태 카드용 요약 (M7 — 목업 "Credential 필요 여부" 행).
    from app.skills.moldy_metadata import (
        credential_requirements_from_metadata,
        load_moldy_metadata,
    )

    metadata, _issues = load_moldy_metadata({f.path: f for f in files})
    credential_requirement_count = len(credential_requirements_from_metadata(metadata))

    return {
        "session_id": str(session.id),
        "mode": session.mode,
        "slug": slug,
        "file_count": len(files),
        "files": [{"path": f.path, "size": len(f.content)} for f in files[:100]],
        "changed_count": changed + deleted,
        "credential_requirement_count": credential_requirement_count,
    }


async def gc_stale_draft_workspaces(db: AsyncSession, *, retention_hours: int) -> int:
    """완료/포기된 세션의 워크스페이스와 세션 없는 orphan 디렉토리를 정리한다.

    mtime이 아니라 **세션 상태 기준** (스펙 AD-2): ``active``/``confirming``
    세션은 abandon 지평(``skill_draft_abandon_days``, 기본 14일) 안에서는
    보존한다 (브라우저를 닫았다 며칠 뒤 돌아와도 재개). 대화가 소실됐거나
    지평을 넘긴 비완료 세션은 ``abandoned``로 전이해 다음 패스에서 회수한다
    (R2 — 전이 경로가 없으면 이탈 세션이 영구 누수).
    ``completed``/``abandoned``만 ``updated_at``이 리텐션을 지나면 삭제하고
    ``draft_workspace_path``를 비운다. 세션 row가 없는 디렉토리(커밋 실패
    잔재 등)는 디렉토리 mtime 기준으로 삭제한다. 커밋까지 수행(크론 호출용).
    """

    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    removed = 0

    await _mark_dead_sessions_abandoned(db, cutoff=cutoff)

    result = await db.execute(
        select(SkillBuilderSession).where(
            SkillBuilderSession.status.in_(GC_DELETABLE_STATUSES),
            SkillBuilderSession.updated_at < cutoff,
            SkillBuilderSession.draft_workspace_path.is_not(None),
        )
    )
    for session in result.scalars():
        workspace = resolve_data_path(session.draft_workspace_path or "")
        if workspace.is_dir():
            shutil.rmtree(workspace, ignore_errors=True)
        session.draft_workspace_path = None
        removed += 1
    await db.commit()

    removed += await _gc_orphan_workspace_dirs(db, cutoff=cutoff)
    if removed:
        logger.info("skill draft workspace GC removed %d workspace(s)", removed)
    return removed


async def _mark_dead_sessions_abandoned(db: AsyncSession, *, cutoff: datetime) -> int:
    """죽은/이탈 v2 세션을 ``abandoned``로 전이해 상태 GC의 회수 대상으로 만든다.

    ``abandoned``는 선언만 되고 전이 경로가 없으면 GC_DELETABLE_STATUSES 절반이
    죽은 규칙이 된다(R2 리뷰) — 여기가 유일한 전이 지점이다. 두 부류만 전이:

    1. **죽은 세션** — draft-conversation GC가 대화를 지워 ``conversation_id``가
       SET NULL로 끊긴 비완료 세션(재개 불가). 같은 리텐션 cutoff 적용
       (생성 직후 attach 전 창 보호).
    2. **장기 이탈 세션** — 대화는 남아 있으나 ``skill_draft_abandon_days``
       (기본 14일) 동안 미활동인 비완료 세션. 활성 세션 보존 원칙(AD-2)은
       유지하되 무기한 누수만 막는다.

    v1 레거시 행(워크스페이스 없음)은 건드리지 않도록
    ``draft_workspace_path IS NOT NULL``로 스코프를 좁힌다.
    """

    terminal = ("completed", "abandoned")
    abandon_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=max(1, settings.skill_draft_abandon_days)
    )
    result = await db.execute(
        select(SkillBuilderSession).where(
            SkillBuilderSession.status.not_in(terminal),
            SkillBuilderSession.draft_workspace_path.is_not(None),
            or_(
                and_(
                    SkillBuilderSession.conversation_id.is_(None),
                    SkillBuilderSession.updated_at < cutoff,
                ),
                SkillBuilderSession.updated_at < abandon_cutoff,
            ),
        )
    )
    marked = 0
    for session in result.scalars():
        session.status = "abandoned"
        marked += 1
    if marked:
        await db.flush()
        logger.info("skill draft GC marked %d dead/stale session(s) abandoned", marked)
    return marked


async def _gc_orphan_workspace_dirs(db: AsyncSession, *, cutoff: datetime) -> int:
    """세션 row가 없는 ``skill-drafts/`` 하위 디렉토리 삭제 (mtime 기준)."""

    drafts_root = resolve_data_path(SKILL_DRAFTS_ROOT)
    if not drafts_root.is_dir():
        return 0
    removed = 0
    cutoff_ts = cutoff.replace(tzinfo=UTC).timestamp()
    for entry in drafts_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            session_id = uuid.UUID(entry.name)
        except ValueError:
            session_id = None
        if session_id is not None:
            exists = await db.scalar(
                select(SkillBuilderSession.id).where(SkillBuilderSession.id == session_id)
            )
            if exists is not None:
                continue  # 살아있는 세션 — 상태 기준 GC가 담당.
        try:
            if entry.stat().st_mtime > cutoff_ts:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        except OSError:
            logger.exception("orphan draft workspace GC failed for entry=%s", entry)
    return removed


def role_for_path(path: str) -> str:
    """드래프트 파일 경로 → SkillDraftFile.role (정본 — 스냅샷 로더도 위임)."""

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
