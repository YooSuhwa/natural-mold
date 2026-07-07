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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillDraftFile
from app.storage.paths import ensure_relative, resolve_data_path

if TYPE_CHECKING:
    from app.models.message_attachment import MessageAttachment
    from app.models.skill import Skill

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


async def gc_stale_draft_workspaces(
    db: AsyncSession, *, retention_hours: int
) -> int:
    """완료/포기된 세션의 워크스페이스와 세션 없는 orphan 디렉토리를 정리한다.

    mtime이 아니라 **세션 상태 기준** (스펙 AD-2): ``active``/``confirming``
    세션은 아무리 오래돼도 보존한다 (브라우저를 닫았다 며칠 뒤 돌아와도 재개).
    ``completed``/``abandoned``만 ``updated_at``이 리텐션을 지나면 삭제하고
    ``draft_workspace_path``를 비운다. 세션 row가 없는 디렉토리(커밋 실패
    잔재 등)는 디렉토리 mtime 기준으로 삭제한다. 커밋까지 수행(크론 호출용).
    """

    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    removed = 0

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
