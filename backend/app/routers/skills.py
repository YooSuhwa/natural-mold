from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, CAP, RES, authz_policy
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.skill_access import (
    CREATE_SKILL,
    SKILL_MIN_VISIBLE,
    authorize_skill,
    require_create_skill,
    skill_created,
    skill_deleted,
    visible_skills,
)
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import invalid_skill_package, skill_feedback_invalid
from app.routers.skill_router_support import (
    record_revision_create_audits,
    record_skill_audit,
    serialize_skill,
    serialize_skills,
)
from app.schemas.resource_view_context import ResourceViewContext
from app.schemas.skill import (
    SkillContentUpdate,
    SkillCreate,
    SkillMetadataUpdate,
    SkillResponse,
    SkillTextContentResponse,
)
from app.schemas.skill_builder import JsonValue
from app.schemas.skill_feedback import (
    SkillFeedbackMineResponse,
    SkillFeedbackSummaryResponse,
    SkillFeedbackUpsertRequest,
)
from app.schemas.skill_usage import SkillUsageDailyPointResponse, SkillUsageSummaryResponse
from app.services import skill_feedback_service, skill_revision_mutations, skill_usage_service
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation
from app.skills.inspector import SkillMetadataError
from app.skills.package_exporter import build_installed_skill_zip_bytes

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("", response_model=list[SkillResponse])
@authz_policy(CANDIDATES("skill", "can_use"))
async def list_skills(
    kind: str | None = Query(default=None, pattern="^(text|package)$"),
    q: str | None = Query(default=None, max_length=120),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    skills = await visible_skills(db, authz, context, kind=kind, query=q)
    return await serialize_skills(db, skills, ResourceViewContext(user, context))


@router.post("", response_model=SkillResponse, status_code=201)
@authz_policy(CAP(CREATE_SKILL))
async def create_text_skill(
    data: SkillCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    await require_create_skill(authz, context)
    try:
        skill = await skill_service.create_text_skill(
            db,
            TextSkillCreation(
                name=data.name,
                slug=data.slug,
                description=data.description,
                content=data.content,
                version=data.version,
            ),
            context,
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    created = await skill_created(db, authz, skill)
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
    await apply_lifecycle(created, authz)  # read-your-writes before responding (8.6)
    return await serialize_skill(db, skill, ResourceViewContext(user, context))


@router.get("/{skill_id}", response_model=SkillResponse)
@authz_policy(RES("skill", "can_view", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_view")
    return await serialize_skill(db, skill, ResourceViewContext(user, context))


@router.patch("/{skill_id}", response_model=SkillResponse)
@authz_policy(RES("skill", "can_edit", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def patch_skill_metadata(
    skill_id: uuid.UUID,
    data: SkillMetadataUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_edit")
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    changed_fields = sorted(data.model_fields_set)
    changed_fields_json: list[JsonValue] = list(changed_fields)
    updated = await skill_service.update_metadata(
        db,
        skill=skill,
        context=context,
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
        metadata_json={"changed_fields": changed_fields_json},
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
    return await serialize_skill(db, updated, ResourceViewContext(user, context))


@router.put("/{skill_id}/content", response_model=SkillResponse)
@authz_policy(RES("skill", "can_edit", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def put_text_content(
    skill_id: uuid.UUID,
    data: SkillContentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_edit")
    if skill.kind != "text":
        raise invalid_skill_package("only text skills support content updates")
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    try:
        updated = await skill_service.update_text_content(
            db, skill=skill, content=data.content, context=context
        )
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
    return await serialize_skill(db, updated, ResourceViewContext(user, context))


@router.get("/{skill_id}/content", response_model=SkillTextContentResponse)
@authz_policy(RES("skill", "can_view", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def get_text_content(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_view")
    if skill.kind != "text":
        raise invalid_skill_package("only text skills expose plain content")
    content = await skill_service.read_text_content(skill)
    return SkillTextContentResponse(content=content)


@router.get("/{skill_id}/feedback", response_model=SkillFeedbackSummaryResponse)
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def get_skill_feedback(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
    return await _feedback_summary_response(db, skill_id=skill.id, user_id=user.id)


@router.put("/{skill_id}/feedback", response_model=SkillFeedbackSummaryResponse)
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def upsert_skill_feedback(
    skill_id: uuid.UUID,
    data: SkillFeedbackUpsertRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
    try:
        await skill_feedback_service.upsert_skill_feedback(
            db,
            skill_id=skill.id,
            user_id=user.id,
            rating=data.rating,
            comment=data.comment,
        )
    except skill_feedback_service.SkillFeedbackInvalid as exc:
        raise skill_feedback_invalid(str(exc)) from exc
    await db.commit()
    return await _feedback_summary_response(db, skill_id=skill.id, user_id=user.id)


@router.delete("/{skill_id}/feedback", status_code=204)
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def delete_skill_feedback(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
    # Idempotent — deleting absent feedback is not an error.
    await skill_feedback_service.delete_skill_feedback(db, skill_id=skill.id, user_id=user.id)
    await db.commit()


async def _feedback_summary_response(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SkillFeedbackSummaryResponse:
    summary = await skill_feedback_service.get_skill_feedback_summary(
        db,
        skill_id=skill_id,
        user_id=user_id,
    )
    return SkillFeedbackSummaryResponse(
        skill_id=skill_id,
        up_count=summary.up_count,
        down_count=summary.down_count,
        mine=(
            SkillFeedbackMineResponse.model_validate(summary.mine)
            if summary.mine is not None
            else None
        ),
    )


@router.get("/{skill_id}/usage", response_model=SkillUsageSummaryResponse)
@authz_policy(RES("skill", "can_view", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def get_skill_usage(
    skill_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_view")
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
@authz_policy(RES("skill", "can_view", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def export_package_skill(
    skill_id: uuid.UUID,
    include_evals: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    skill = await authorize_skill(db, authz, context, skill_id, "can_view")
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
@authz_policy(RES("skill", "can_delete", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def delete_skill(
    skill_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    skill = await authorize_skill(db, authz, context, skill_id, "can_delete")
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.delete",
        skill=skill,
    )
    deleted = await skill_deleted(db, authz, skill)
    await skill_service.delete_skill(db, skill, context)
    await db.commit()
    await apply_lifecycle(deleted, authz)
