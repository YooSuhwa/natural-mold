from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.skill import SkillCreate, SkillResponse, SkillUpdate
from app.services import skill_service

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await skill_service.list_skills(db, user.id)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await skill_service.create_skill(db, data, user.id)


@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_skill(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a .skill package (ZIP with SKILL.md frontmatter)."""
    file_data = await file.read()
    try:
        return await skill_service.upload_skill_package(db, file_data, user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/{skill_id}/files/{file_path:path}")
async def get_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Serve a file from a package skill's extracted directory."""
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill or not skill.storage_path:
        raise HTTPException(status_code=404, detail="Skill not found")

    base = Path(skill.storage_path).resolve()
    target = (base / file_path).resolve()

    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: uuid.UUID,
    data: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return await skill_service.update_skill(db, skill, data)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    await skill_service.delete_skill(db, skill)
