from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import skill_not_found
from app.marketplace import credential_requirements
from app.routers.skill_router_support import record_skill_audit
from app.schemas.skill import (
    SkillCredentialBindingIn,
    SkillCredentialBindingOut,
    SkillCredentialRequirementOut,
)
from app.skills import service as skill_service

router = APIRouter(prefix="/api/skills/{skill_id}", tags=["skills"])


@router.get(
    "/credential-requirements",
    response_model=list[SkillCredentialRequirementOut],
)
async def get_skill_credential_requirements(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillCredentialRequirementOut]:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return [
        SkillCredentialRequirementOut(
            key=requirement.key,
            definition_key=requirement.definition_key,
            required=requirement.required,
            label=requirement.label,
            description=requirement.description,
            fields=list(requirement.fields),
            injection=requirement.injection,  # type: ignore[arg-type]
            scope=requirement.scope,  # type: ignore[arg-type]
        )
        for requirement in credential_requirements.parse_requirements(skill)
    ]


@router.get(
    "/credential-bindings",
    response_model=list[SkillCredentialBindingOut],
)
async def list_skill_credential_bindings(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillCredentialBindingOut]:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    rows = await credential_requirements.list_bindings(db, skill=skill, user=user)
    return [SkillCredentialBindingOut.model_validate(row) for row in rows]


@router.put(
    "/credential-bindings/{requirement_key}",
    response_model=SkillCredentialBindingOut,
)
async def put_skill_credential_binding(
    skill_id: uuid.UUID,
    requirement_key: str,
    body: SkillCredentialBindingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillCredentialBindingOut:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    row = await credential_requirements.upsert_binding(
        db,
        skill=skill,
        user=user,
        requirement_key=requirement_key,
        credential_id=body.credential_id,
    )
    await db.commit()
    await db.refresh(row)
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.credential_binding_upsert",
        skill=skill,
        metadata={"requirement_key": requirement_key, "credential_id": str(body.credential_id)},
    )
    await db.commit()
    return SkillCredentialBindingOut.model_validate(row)


@router.delete(
    "/credential-bindings/{requirement_key}",
    status_code=204,
)
async def delete_skill_credential_binding(
    skill_id: uuid.UUID,
    requirement_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    deleted = await credential_requirements.delete_binding(
        db,
        skill=skill,
        user=user,
        requirement_key=requirement_key,
    )
    if not deleted:
        return Response(status_code=204)
    await record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.credential_binding_delete",
        skill=skill,
        metadata={"requirement_key": requirement_key},
    )
    await db.commit()
    return Response(status_code=204)
