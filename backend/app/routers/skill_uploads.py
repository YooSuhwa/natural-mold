from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import invalid_skill_package, marketplace_secret_detected
from app.marketplace.secret_scan import scan_package
from app.routers.skill_evaluation_prepare_support import record_preparation_audit
from app.routers.skill_router_support import (
    record_revision_create_audits,
    record_skill_audit,
    serialize_skill,
)
from app.schemas.skill import SkillResponse
from app.services import skill_revision_mutations
from app.services.skill_evaluation_auto_preparation import (
    prepare_skill_evaluation_set_best_effort,
)
from app.skills import service as skill_service
from app.skills.packager import PackageError
from app.storage.paths import resolve_data_path

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_package_skill(
    file: UploadFile,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillResponse:
    file_data = await file.read()
    try:
        skill = await skill_service.create_package_skill(
            db,
            user_id=user.id,
            zip_bytes=file_data,
        )
    except PackageError as exc:
        raise invalid_skill_package(str(exc)) from None
    if skill.storage_path is None:
        await skill_service.delete_skill(db, skill)
        await db.rollback()
        raise invalid_skill_package("missing skill storage path")
    skill_path = resolve_data_path(skill.storage_path)
    skills_root = (Path(settings.data_root) / "skills").resolve()
    if not skill_path.is_relative_to(skills_root):
        await skill_service.delete_skill(db, skill)
        await db.rollback()
        raise invalid_skill_package("invalid skill storage path")
    findings = scan_package(skill_path)
    if findings:
        await skill_service.delete_skill(db, skill)
        await db.rollback()
        summary = ", ".join(f"{finding.path} ({finding.kind})" for finding in findings[:5])
        raise marketplace_secret_detected(f"package contains potential secrets: {summary}")
    revision = await skill_revision_mutations.create_initial_revision(
        db,
        skill=skill,
        user_id=user.id,
        metadata_json={"upload": True},
    )
    preparation_result = await prepare_skill_evaluation_set_best_effort(
        db=db,
        skill=skill,
        user_id=user.id,
        source_kind="package_import",
        allow_llm_generation=True,
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
        metadata={
            "upload": True,
            "filename": file.filename,
            "package_file_count": len((skill.package_metadata or {}).get("files") or []),
        },
    )
    await record_revision_create_audits(
        db,
        user=user,
        request=request,
        baseline=None,
        revision=revision,
    )
    await record_preparation_audit(
        db,
        user=user,
        request=request,
        skill_id=skill.id,
        result=preparation_result,
    )
    await db.commit()
    return await serialize_skill(db, skill, user)
