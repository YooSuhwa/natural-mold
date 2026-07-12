from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import invalid_skill_package, skill_not_found
from app.routers.skill_router_support import (
    record_revision_create_audits,
    record_skill_audit,
    serialize_skill,
    serialize_skills,
)
from app.schemas.skill import (
    SkillContentUpdate,
    SkillCreate,
    SkillMetadataUpdate,
    SkillResponse,
    SkillTextContentResponse,
)
from app.schemas.skill_usage import SkillUsageDailyPointResponse, SkillUsageSummaryResponse
from app.services import skill_revision_mutations, skill_usage_service
from app.skills import service as skill_service
from app.skills.inspector import SkillMetadataError
from app.skills.package_exporter import build_installed_skill_zip_bytes

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    kind: str | None = Query(default=None, pattern="^(text|package)$"),
    q: str | None = Query(default=None, max_length=120),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skills = await skill_service.list_skills(db, user.id, kind=kind, query=q)
    return await serialize_skills(db, skills, user)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_text_skill(
    data: SkillCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    try:
        skill = await skill_service.create_text_skill(
            db,
            user_id=user.id,
            name=data.name,
            slug=data.slug,
            description=data.description,
            content=data.content,
            version=data.version,
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    revision = await skill_revision_mutations.create_initial_revision(
        db,
        skill=skill,
        user_id=user.id,
    )
    await db.commit()
    await db.refresh(skill)
    await db.refresh(revision)
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.create",
        skill=skill,
        metadata={"content_length": len(data.content)},
    )
    await record_revision_create_audits(
        db,
        user=user,
        request=request,
        baseline=None,
        revision=revision,
    )
    await db.commit()
    return await serialize_skill(db, skill, user)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return await serialize_skill(db, skill, user)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def patch_skill_metadata(
    skill_id: uuid.UUID,
    data: SkillMetadataUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    changed_fields = sorted(data.model_fields_set)
    updated = await skill_service.update_metadata(
        db,
        skill=skill,
        name=data.name,
        description=data.description,
        version=data.version,
    )
    revision = await skill_revision_mutations.create_manual_revision(
        db,
        skill=updated,
        user_id=user.id,
        operation="manual_metadata_update",
        parent_revision_id=revision_parent.parent_revision_id,
        metadata_json={"changed_fields": changed_fields},
    )
    await db.commit()
    await db.refresh(updated)
    await db.refresh(revision)
    await record_revision_create_audits(
        db,
        user=user,
        request=request,
        baseline=revision_parent.baseline_revision,
        revision=revision,
    )
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.update",
        skill=updated,
        metadata={"changed_fields": changed_fields},
    )
    await db.commit()
    return await serialize_skill(db, updated, user)


@router.put("/{skill_id}/content", response_model=SkillResponse)
async def put_text_content(
    skill_id: uuid.UUID,
    data: SkillContentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "text":
        raise invalid_skill_package("only text skills support content updates")
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    try:
        updated = await skill_service.update_text_content(db, skill=skill, content=data.content)
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    revision = await skill_revision_mutations.create_manual_revision(
        db,
        skill=updated,
        user_id=user.id,
        operation="manual_content_update",
        parent_revision_id=revision_parent.parent_revision_id,
        changed_files=[{"path": "SKILL.md", "operation": "update"}],
    )
    await db.commit()
    await db.refresh(updated)
    await db.refresh(revision)
    await record_revision_create_audits(
        db,
        user=user,
        request=request,
        baseline=revision_parent.baseline_revision,
        revision=revision,
    )
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.content_update",
        skill=updated,
        metadata={"content_length": len(data.content)},
    )
    await db.commit()
    return await serialize_skill(db, updated, user)


@router.get("/{skill_id}/content", response_model=SkillTextContentResponse)
async def get_text_content(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "text":
        raise invalid_skill_package("only text skills expose plain content")
    content = await skill_service.read_text_content(skill)
    return SkillTextContentResponse(content=content)


@router.get("/{skill_id}/usage", response_model=SkillUsageSummaryResponse)
async def get_skill_usage(
    skill_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    summary = await skill_usage_service.get_skill_usage_summary(db, skill_id=skill.id, days=days)
    return SkillUsageSummaryResponse(
        skill_id=skill.id,
        days=summary.days,
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
        cost_usd=summary.cost_usd,
        priced_event_count=summary.priced_event_count,
        unpriced_token_event_count=summary.unpriced_token_event_count,
        evaluation_run_count=summary.evaluation_run_count,
        chat_execution_count=summary.chat_execution_count,
        daily=[
            SkillUsageDailyPointResponse(
                date=point.date,
                tokens_in=point.tokens_in,
                tokens_out=point.tokens_out,
                cost_usd=point.cost_usd,
                execution_count=point.execution_count,
            )
            for point in summary.daily
        ],
    )


@router.get("/{skill_id}/export")
async def export_package_skill(
    skill_id: uuid.UUID,
    include_evals: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package("only package skills can be exported")
    try:
        zip_bytes = build_installed_skill_zip_bytes(skill, include_evals=include_evals)
    except ValueError as exc:
        raise invalid_skill_package(str(exc)) from None
    filename = f"{skill_service.slugify(skill.slug or skill.name)}.skill"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.delete",
        skill=skill,
    )
    await skill_service.delete_skill(db, skill)
    await db.commit()
