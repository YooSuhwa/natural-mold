"""Account, scope, invitation and bootstrap roles commit together in the auth router."""

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
from app.services.registration_policy import prepare_registration


async def register_user(db: AsyncSession, payload: RegisterRequest, request: Request) -> User:
    scope = await prepare_registration(db, payload)
    if await user_service.email_exists(db, payload.email):
        raise AppError(code="email_already_exists", message="이미 가입된 이메일입니다", status=409)
    promote = settings.allow_first_user_as_admin and await user_service.is_first_user(db)
    user = await user_service.create_user(
        db,
        email=payload.email,
        password_hash=await run_sync(hash_password, payload.password),
        name=payload.profile_display_name,
        display_name=payload.profile_display_name,
        is_super_user=promote,
    )
    if scope.invitation is None or promote:
        await enroll_default_organization(
            db, DefaultEnrollment(scope.default, user, promote), request
        )
    if payload.invitation_token is not None:
        await accept_registration_invitation(
            RegistrationInvitationAcceptance(
                OrgMutationRequest(db=db, request=request, expected_version=None),
                scope,
                user,
                payload.invitation_token,
            )
        )
    return user
