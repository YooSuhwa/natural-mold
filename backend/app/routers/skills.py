"""Skills API — text + package CRUD, file tree, file content."""

from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Depends, Form, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import (
    invalid_file_path,
    invalid_skill_package,
    skill_file_not_found,
    skill_not_found,
)
from app.schemas.skill import (
    SkillContentUpdate,
    SkillCreate,
    SkillFileEntry,
    SkillFileUpdate,
    SkillMetadataUpdate,
    SkillResponse,
    SkillTextContentResponse,
)
from app.skills import service as skill_service
from app.skills.packager import PackageError

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    kind: str | None = Query(default=None, pattern="^(text|package)$"),
    q: str | None = Query(default=None, max_length=120),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await skill_service.list_skills(db, user.id, kind=kind, query=q)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_text_skill(
    data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.create_text_skill(
        db,
        user_id=user.id,
        name=data.name,
        slug=data.slug,
        description=data.description,
        content=data.content,
        version=data.version,
    )
    await db.commit()
    await db.refresh(skill)
    return skill


@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_package_skill(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a ``.skill`` package (ZIP with SKILL.md frontmatter)."""

    file_data = await file.read()
    try:
        skill = await skill_service.create_package_skill(
            db,
            user_id=user.id,
            zip_bytes=file_data,
        )
    except PackageError as exc:
        raise invalid_skill_package(str(exc)) from None
    await db.commit()
    await db.refresh(skill)
    return skill


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return skill


@router.patch("/{skill_id}", response_model=SkillResponse)
async def patch_skill_metadata(
    skill_id: uuid.UUID,
    data: SkillMetadataUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    updated = await skill_service.update_metadata(
        db,
        skill=skill,
        name=data.name,
        description=data.description,
        version=data.version,
    )
    await db.commit()
    await db.refresh(updated)
    return updated


@router.put("/{skill_id}/content", response_model=SkillResponse)
async def put_text_content(
    skill_id: uuid.UUID,
    data: SkillContentUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "text":
        raise invalid_skill_package("only text skills support content updates")
    updated = await skill_service.update_text_content(
        db, skill=skill, content=data.content
    )
    await db.commit()
    await db.refresh(updated)
    return updated


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


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    await skill_service.delete_skill(db, skill)
    await db.commit()


@router.get("/{skill_id}/files", response_model=list[SkillFileEntry])
async def list_skill_files(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return [
        SkillFileEntry(path=item.path, size=item.size, is_dir=item.is_dir)
        for item in skill_service.get_skill_files(skill)
    ]


@router.get("/{skill_id}/files/{file_path:path}")
async def get_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    try:
        data = skill_service.get_file_bytes(skill, file_path)
    except FileNotFoundError:
        raise skill_file_not_found() from None
    except ValueError:
        raise invalid_file_path() from None
    media_type, _ = mimetypes.guess_type(file_path)
    return Response(content=data, media_type=media_type or "application/octet-stream")


# -- file-level mutations (M-SKILL1) ----------------------------------------


@router.put("/{skill_id}/files/{file_path:path}", response_model=SkillResponse)
async def put_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    data: SkillFileUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create or overwrite a single file in a package skill."""

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package(
            "file-level mutations are only valid for package skills"
        )
    try:
        updated = await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path=file_path,
            content=data.content.encode("utf-8"),
        )
    except ValueError as exc:
        raise invalid_file_path() from exc
    await db.commit()
    await db.refresh(updated)
    return updated


@router.delete("/{skill_id}/files/{file_path:path}", response_model=SkillResponse)
async def delete_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Delete a file (or directory) from a package skill. SKILL.md is protected."""

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package(
            "file-level mutations are only valid for package skills"
        )
    try:
        updated = await skill_service.delete_skill_file(
            db, skill=skill, rel_path=file_path
        )
    except ValueError as exc:
        raise invalid_file_path() from exc
    await db.commit()
    await db.refresh(updated)
    return updated


@router.post("/{skill_id}/files", response_model=SkillResponse, status_code=201)
async def upload_skill_file(
    skill_id: uuid.UUID,
    file: UploadFile,
    rel_path: str = Form(..., description="Relative path inside the skill, eg 'scripts/run.py'"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a binary or text file (multipart) into a package skill."""

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package(
            "file-level mutations are only valid for package skills"
        )
    body = await file.read()
    try:
        updated = await skill_service.set_skill_file(
            db, skill=skill, rel_path=rel_path, content=body
        )
    except ValueError as exc:
        raise invalid_file_path() from exc
    await db.commit()
    await db.refresh(updated)
    return updated
