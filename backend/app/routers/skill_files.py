from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.skill_access import SKILL_MIN_VISIBLE, authorize_skill
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import (
    invalid_file_path,
    invalid_skill_package,
    skill_file_not_found,
)
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.routers.skill_router_support import (
    record_revision_create_audits,
    record_skill_audit,
    serialize_skill,
)
from app.schemas.resource_view_context import ResourceViewContext
from app.schemas.skill import SkillFileEntry, SkillFileUpdate, SkillResponse
from app.services import skill_revision_mutations
from app.skills import service as skill_service
from app.skills.inspector import SkillMetadataError

router = APIRouter(prefix="/api/skills/{skill_id}/files", tags=["skills"])


@router.get("", response_model=list[SkillFileEntry])
@authz_policy(RES("skill", "can_view", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def list_skill_files(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[SkillFileEntry]:
    skill = await authorize_skill(db, authz, context, skill_id, "can_view")
    return [
        SkillFileEntry(path=item.path, size=item.size, is_dir=item.is_dir)
        for item in skill_service.get_skill_files(skill)
    ]


@router.get("/{file_path:path}")
@authz_policy(RES("skill", "can_view", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def get_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    skill = await authorize_skill(db, authz, context, skill_id, "can_view")
    try:
        data = skill_service.get_file_bytes(skill, file_path)
    except FileNotFoundError:
        raise skill_file_not_found() from None
    except ValueError:
        raise invalid_file_path() from None
    media_type, _ = mimetypes.guess_type(file_path)
    return Response(content=data, media_type=media_type or "application/octet-stream")


@router.put("/{file_path:path}", response_model=SkillResponse)
@authz_policy(RES("skill", "can_edit", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def put_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    data: SkillFileUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> SkillResponse:
    skill = await _load_package_skill(db, authz, context, skill_id)
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    try:
        updated = await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path=file_path,
            content=data.content.encode("utf-8"),
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    except ValueError as exc:
        raise invalid_file_path() from exc
    revision = await _record_file_revision(
        db,
        skill=updated,
        user_id=user.id,
        parent=revision_parent,
        path=file_path,
        action="upsert",
    )
    await db.commit()
    await db.refresh(updated)
    await _record_file_audits(
        db,
        user=user,
        request=request,
        skill=updated,
        baseline=revision_parent.baseline_revision,
        revision=revision,
        action="skill.file_update",
        metadata={"file_path": file_path, "content_length": len(data.content)},
    )
    await db.commit()
    return await serialize_skill(db, updated, ResourceViewContext(user, context))


@router.delete("/{file_path:path}", response_model=SkillResponse)
@authz_policy(RES("skill", "can_edit", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def delete_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> SkillResponse:
    skill = await _load_package_skill(db, authz, context, skill_id)
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    try:
        updated = await skill_service.delete_skill_file(db, skill=skill, rel_path=file_path)
    except ValueError as exc:
        raise invalid_file_path() from exc
    revision = await _record_file_revision(
        db,
        skill=updated,
        user_id=user.id,
        parent=revision_parent,
        path=file_path,
        action="delete",
    )
    await db.commit()
    await db.refresh(updated)
    await _record_file_audits(
        db,
        user=user,
        request=request,
        skill=updated,
        baseline=revision_parent.baseline_revision,
        revision=revision,
        action="skill.file_delete",
        metadata={"file_path": file_path},
    )
    await db.commit()
    return await serialize_skill(db, updated, ResourceViewContext(user, context))


@router.post("", response_model=SkillResponse, status_code=201)
@authz_policy(RES("skill", "can_edit", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def upload_skill_file(
    skill_id: uuid.UUID,
    file: UploadFile,
    request: Request,
    rel_path: str = Form(..., description="Relative path inside the skill, eg 'scripts/run.py'"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> SkillResponse:
    skill = await _load_package_skill(db, authz, context, skill_id)
    body = await file.read()
    revision_parent = await skill_revision_mutations.prepare_mutation_parent(
        db,
        skill=skill,
        user_id=user.id,
    )
    try:
        updated = await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path=rel_path,
            content=body,
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    except ValueError as exc:
        raise invalid_file_path() from exc
    revision = await _record_file_revision(
        db,
        skill=updated,
        user_id=user.id,
        parent=revision_parent,
        path=rel_path,
        action="upload",
    )
    await db.commit()
    await db.refresh(updated)
    await _record_file_audits(
        db,
        user=user,
        request=request,
        skill=updated,
        baseline=revision_parent.baseline_revision,
        revision=revision,
        action="skill.file_upload",
        metadata={"file_path": rel_path, "filename": file.filename, "content_length": len(body)},
    )
    await db.commit()
    return await serialize_skill(db, updated, ResourceViewContext(user, context))


async def _load_package_skill(
    db: AsyncSession, authz: AuthzService, context: OrgContext, skill_id: uuid.UUID
) -> Skill:
    skill = await authorize_skill(db, authz, context, skill_id, "can_edit")
    if skill.kind != "package":
        raise invalid_skill_package("file-level mutations are only valid for package skills")
    return skill


async def _record_file_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    parent: skill_revision_mutations.MutationRevisionParent,
    path: str,
    action: str,
) -> SkillRevision:
    return await skill_revision_mutations.create_manual_revision(
        db,
        skill=skill,
        user_id=user_id,
        operation="manual_file_update",
        parent_revision_id=parent.parent_revision_id,
        changed_files=[{"path": path, "operation": action}],
    )


async def _record_file_audits(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    skill: Skill,
    baseline: SkillRevision | None,
    revision: SkillRevision,
    action: str,
    metadata: dict[str, object],
) -> None:
    await record_revision_create_audits(
        db,
        user=user,
        request=request,
        baseline=baseline,
        revision=revision,
    )
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action=action,
        skill=skill,
        metadata=metadata,
    )
