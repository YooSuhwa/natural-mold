from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.skill_access import SKILL_MIN_VISIBLE, authorize_skill
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.marketplace import credential_requirements
from app.routers.skill_router_support import record_skill_audit
from app.schemas.skill import (
    SkillCredentialBindingIn,
    SkillCredentialBindingOut,
    SkillCredentialRequirementOut,
)

router = APIRouter(prefix="/api/skills/{skill_id}", tags=["skills"])


@router.get(
    "/credential-requirements",
    response_model=list[SkillCredentialRequirementOut],
)
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def get_skill_credential_requirements(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[SkillCredentialRequirementOut]:
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
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
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def list_skill_credential_bindings(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[SkillCredentialBindingOut]:
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
    rows = await credential_requirements.list_bindings(db, skill=skill, context=context)
    return [SkillCredentialBindingOut.model_validate(row) for row in rows]


@router.put(
    "/credential-bindings/{requirement_key}",
    response_model=SkillCredentialBindingOut,
)
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def put_skill_credential_binding(
    skill_id: uuid.UUID,
    requirement_key: str,
    body: SkillCredentialBindingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> SkillCredentialBindingOut:
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
    # Bindings stay per-user rows (SELF semantics in the service). TODO(D-04/F-05):
    # require credential#can_use on body.credential_id through AuthzService.
    row = await credential_requirements.upsert_binding(
        db,
        skill=skill,
        context=context,
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
@authz_policy(RES("skill", "can_use", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def delete_skill_credential_binding(
    skill_id: uuid.UUID,
    requirement_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    skill = await authorize_skill(db, authz, context, skill_id, "can_use")
    deleted = await credential_requirements.delete_binding(
        db,
        skill=skill,
        context=context,
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
