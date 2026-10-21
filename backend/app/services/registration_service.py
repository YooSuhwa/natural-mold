"""Account and typed signup scope mutations share the auth transaction."""

from dataclasses import dataclass
from typing import assert_never
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.config import settings
from app.exceptions import AppError
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.services import user_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_acceptance import (
    RegistrationInvitationAcceptance,
    accept_registration_invitation,
)
from app.services.registration_membership import DefaultEnrollment, enroll_default_organization
from app.services.registration_personal import create_personal_tenant
from app.services.registration_policy import (
    DefaultRegistrationScope,
    PersonalRegistrationScope,
    RegistrationScope,
    TenantInvitationRegistrationScope,
    prepare_registration,
)
from app.services.tenants.invitation_acceptance import (
    RegistrationTenantInvitationAcceptance,
    accept_registration_tenant_invitation,
)


@dataclass(frozen=True, slots=True)
class Registration:
    user: User
    personal_org_id: UUID | None  # set when the signup created the account's own company


async def register_user(db: AsyncSession, payload: RegisterRequest, request: Request) -> User:
    return (await register(db, payload, request)).user


async def register(db: AsyncSession, payload: RegisterRequest, request: Request) -> Registration:
    with db.no_autoflush:
        scope = await prepare_registration(db, payload)
        if await user_service.email_exists(db, payload.email):
            raise AppError(
                code="email_already_exists", message="이미 가입된 이메일입니다", status=409
            )
        promote = settings.allow_first_user_as_admin and await user_service.is_first_user(db)
    user = await user_service.create_user(
        db,
        email=payload.email,
        password_hash=await run_sync(hash_password, payload.password),
        name=payload.profile_display_name,
        display_name=payload.profile_display_name,
        is_super_user=promote,
    )
    mutation = OrgMutationRequest(db=db, request=request, expected_version=None)
    personal_org_id = None
    match scope:
        case PersonalRegistrationScope(default=default) if promote:
            # The first account still administers the installation's default company.
            await enroll_default_organization(db, DefaultEnrollment(default, user, True), request)
        case PersonalRegistrationScope():
            personal = await create_personal_tenant(db, user, request)
            personal_org_id = personal.organization.id
        case DefaultRegistrationScope(default=default):
            await enroll_default_organization(
                db, DefaultEnrollment(default, user, promote), request
            )
        case RegistrationScope():
            if promote:
                await enroll_default_organization(
                    db, DefaultEnrollment(scope.default, user, True), request
                )
            token = payload.invitation_token
            if token is None:
                raise AppError(
                    code="invitation_invalid", message="초대 링크가 필요합니다", status=404
                )
            await accept_registration_invitation(
                RegistrationInvitationAcceptance(mutation, scope, user, token)
            )
        case TenantInvitationRegistrationScope():
            token = payload.invitation_token
            if token is None:
                raise AppError(
                    code="tenant_invitation_invalid",
                    message="회사 초대 링크가 필요합니다",
                    status=404,
                )
            await accept_registration_tenant_invitation(
                RegistrationTenantInvitationAcceptance(
                    mutation, scope.target, scope.invitation, user, token
                )
            )
        case unreachable:
            assert_never(unreachable)
    return Registration(user, personal_org_id)
