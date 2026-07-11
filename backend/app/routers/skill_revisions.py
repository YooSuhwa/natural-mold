from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    skill_file_not_found,
    skill_not_found,
    skill_revision_not_found,
    skill_revision_snapshot_unavailable,
)
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.routers.skill_router_support import serialize_skill
from app.schemas.skill_revision import (
    SkillRevisionDetail,
    SkillRevisionFileContentResponse,
    SkillRevisionFileEntry,
    SkillRevisionFilesResponse,
    SkillRevisionSummary,
    SkillRollbackResponse,
)
from app.services import audit_service, skill_revision_audit, skill_revision_service
from app.skills import service as skill_service

router = APIRouter(prefix="/api/skills/{skill_id}/revisions", tags=["skill-revisions"])


@router.get("", response_model=list[SkillRevisionSummary])
async def list_skill_revisions(
    skill_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillRevisionSummary]:
    # 리비전 목록 — 최신순 상한(형제 세션 목록과 동일한 bounded 계약).
    # 저장/롤백마다 리비전이 늘므로 무제한 반환은 수백 행 응답·DOM으로 자란다.
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revisions = await skill_revision_service.list_revisions(
        db, skill=skill, user_id=user.id, limit=limit
    )
    return [SkillRevisionSummary.model_validate(revision) for revision in revisions]


@router.get("/{revision_id}", response_model=SkillRevisionDetail)
async def get_skill_revision(
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillRevisionDetail:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revision = await skill_revision_service.get_revision(
        db,
        skill=skill,
        user_id=user.id,
        revision_id=revision_id,
    )
    if revision is None:
        raise skill_revision_not_found()
    return SkillRevisionDetail.model_validate(revision)


@router.get("/{revision_id}/files", response_model=SkillRevisionFilesResponse)
async def list_skill_revision_files(
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillRevisionFilesResponse:
    """리비전 스냅샷 zip의 파일 목록 (버전 diff/소스 보기, Phase 2).

    디스크 추출 없이 zip 메타데이터 + head sniff만 읽는다. pruned 스냅샷은
    파일을 제공할 수 없음을 명시 플래그로 반환한다.
    """

    revision = await _load_revision_or_404(
        db, skill_id=skill_id, revision_id=revision_id, user=user
    )
    if skill_revision_service.snapshot_pruned(revision):
        return SkillRevisionFilesResponse(snapshot_pruned=True, files=[])
    entries = await skill_revision_service.list_revision_files(revision)
    if entries is None:
        # pruned 플래그 없이 zip만 유실된 스냅샷 — 500 대신 pruned와 동일 계약.
        return SkillRevisionFilesResponse(snapshot_pruned=True, files=[])
    return SkillRevisionFilesResponse(
        snapshot_pruned=False,
        files=[
            SkillRevisionFileEntry(path=path, size=size, is_binary=is_binary)
            for path, size, is_binary in entries
        ],
    )


@router.get("/{revision_id}/files/content", response_model=SkillRevisionFileContentResponse)
async def get_skill_revision_file_content(
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    path: str = Query(..., min_length=1, max_length=500),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillRevisionFileContentResponse:
    """리비전 스냅샷의 단일 파일 텍스트 (소유자 전용).

    요청 path는 zip 열거 경로와 **정확 일치**해야 한다 — traversal은 매칭
    실패(404)로 끝난다. 바이너리·2MB 초과·pruned도 404(fail-closed).
    """

    revision = await _load_revision_or_404(
        db, skill_id=skill_id, revision_id=revision_id, user=user
    )
    if skill_revision_service.snapshot_pruned(revision):
        raise skill_file_not_found()
    content = await skill_revision_service.load_revision_file_content(revision, path)
    if content is None:
        raise skill_file_not_found()
    return SkillRevisionFileContentResponse(path=path, content=content)


@router.post("/{revision_id}/rollback", response_model=SkillRollbackResponse)
async def rollback_skill_revision(
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillRollbackResponse:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revision = await skill_revision_service.get_revision(
        db,
        skill=skill,
        user_id=user.id,
        revision_id=revision_id,
    )
    if revision is None:
        raise skill_revision_not_found()
    try:
        restored = await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=user.id,
            revision=revision,
            changelog_summary=f"Rolled back to revision {revision.revision_number}.",
        )
    except (
        skill_revision_service.SkillRevisionRollbackUnsupported,
        FileNotFoundError,
    ) as exc:
        # pruned 플래그·zip 유실 모두 files API와 동일한 "스냅샷 불가" 계약으로
        # 정규화 — unhandled 500이면 같은 패널의 diff placeholder와 비대칭이다.
        raise skill_revision_snapshot_unavailable() from exc
    await skill_revision_audit.record_revision_create_audit(
        db,
        user=user,
        request=request,
        revision=restored,
    )
    await _record_revision_rollback_audit(
        db,
        user=user,
        request=request,
        skill_id=skill.id,
        restored_revision_id=revision.id,
        new_revision_id=restored.id,
        old_hash=revision.content_hash,
        new_hash=skill.content_hash,
    )
    await db.commit()
    await db.refresh(skill)
    await db.refresh(restored)
    return SkillRollbackResponse(
        # bare model_validate 대신 serializer — used_by_count/health enrichment 정렬.
        skill=await serialize_skill(db, skill, user),
        revision=SkillRevisionSummary.model_validate(restored),
    )


async def _load_skill_or_404(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user: CurrentUser,
) -> Skill:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if skill is None:
        raise skill_not_found()
    return skill


async def _load_revision_or_404(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    user: CurrentUser,
) -> SkillRevision:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revision = await skill_revision_service.get_revision(
        db,
        skill=skill,
        user_id=user.id,
        revision_id=revision_id,
    )
    if revision is None:
        raise skill_revision_not_found()
    return revision


async def _record_revision_rollback_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    skill_id: uuid.UUID,
    restored_revision_id: uuid.UUID,
    new_revision_id: uuid.UUID,
    old_hash: str | None,
    new_hash: str | None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="skill_revision.rollback",
        target_type="skill",
        target_id=skill_id,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "restored_revision_id": str(restored_revision_id),
            "new_revision_id": str(new_revision_id),
            "old_hash": old_hash,
            "new_hash": new_hash,
        },
    )
