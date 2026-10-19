"""Hash-only seven-day invites with idempotent, permission-checked creation."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.auth.jwt import create_org_invitation_token
from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_directory import scoped_group
from app.authz.org_invitation_queries import invitation_query
from app.models.organization_group import OrgInvitation
from app.schemas.org import OrganizationSettings
from app.schemas.org_groups import DirectoryPage, DirectoryQuery
from app.schemas.org_invitations import InvitationCreate, InvitationCreated, InvitationView
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.pagination import decode_cursor, encode_cursor
from app.services.orgs.versioning import bump_version, utc_timestamp


def invitation_view(row: OrgInvitation) -> InvitationView:
    status = "pending"
    if row.revoked_at is not None:
        status = "revoked"
    elif row.accepted_at is not None:
        status = "accepted"
    elif utc_timestamp(row.expires_at) <= datetime.now(UTC):
        status = "expired"
    return InvitationView.model_validate(
        {
            "id": row.id,
            "email": row.email,
            "role": row.role,
            "group_ids": row.group_ids,
            "expires_at": utc_timestamp(row.expires_at),
            "created_at": utc_timestamp(row.created_at),
            "status": status,
        }
    )


def creation_view(row: OrgInvitation) -> InvitationCreated:
    token = create_org_invitation_token(row.id)
    # A signing-key rotation never substitutes a new token for an existing hash.
    if not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row.token_hash):
        token = None
    return InvitationCreated.model_validate({**invitation_view(row).model_dump(), "token": token})


async def list_invitations(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    query: DirectoryQuery,
) -> DirectoryPage[InvitationView]:
    context = await load_org_control(
        mutation.db,
        context.actor,
        OrgControlTarget(
            context.organization.id,
            "can_manage_members",
        ),
    )
    statement = invitation_query(context)
    position = decode_cursor(query.cursor, context.organization.id)
    if position is not None:
        statement = statement.where(OrgInvitation.id > position)
    rows = list(
        (
            await mutation.db.scalars(statement.order_by(OrgInvitation.id).limit(query.limit + 1))
        ).all()
    )
    selected, more = rows[: query.limit], len(rows) > query.limit
    return DirectoryPage[InvitationView](
        items=[invitation_view(row) for row in selected],
        has_more=more,
        next_cursor=encode_cursor(context.organization.id, selected[-1].id) if more else None,
        updated_at=utc_timestamp(context.organization.updated_at),
    )


async def create_invitation(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    payload: InvitationCreate,
) -> tuple[InvitationCreated, bool]:
    context = await load_org_control(
        mutation.db,
        context.actor,
        OrgControlTarget(
            context.organization.id,
            "can_manage_members",
        ),
    )
    groups = sorted(str(key) for key in payload.group_ids)
    for group_id in payload.group_ids:
        group = await scoped_group(mutation.db, context, group_id)
        if group.source != "manual":
            raise authorization_error("policy_locked")
    role = (
        payload.role
        or OrganizationSettings.model_validate(context.organization.settings).default_member_role
    )
    now, email = datetime.now(UTC), str(payload.email).casefold()
    candidates = (
        await mutation.db.scalars(
            invitation_query(context).where(
                OrgInvitation.email == email,
                OrgInvitation.role == role,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
                OrgInvitation.expires_at > now,
            )
        )
    ).all()
    for row in candidates:
        if row.group_ids == groups:
            return creation_view(row), False
    invitation_id = uuid4()
    token = create_org_invitation_token(invitation_id)
    row = OrgInvitation(
        id=invitation_id,
        org_id=context.organization.id,
        tenant_id=context.organization.tenant_id,
        email=email,
        role=role,
        group_ids=groups,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_at=now,
        expires_at=now + timedelta(days=7),
        invited_by=context.actor.id,
    )
    mutation.db.add(row)
    bump_version(context.organization)
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=audit_actions.ORG_INVITATION_CREATE,
        target_type="org_invitation",
        target_id=row.id,
        target_name_snapshot=email,
        outcome="success",
        request=mutation.request,
        metadata={"org_id": str(context.organization.id), "role": role, "group_count": len(groups)},
    )
    await mutation.db.commit()
    return creation_view(row), True


async def revoke_invitation(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    invitation_id: UUID,
) -> None:
    context = await load_org_control(
        mutation.db,
        context.actor,
        OrgControlTarget(
            context.organization.id,
            "can_manage_members",
        ),
    )
    row = await mutation.db.scalar(
        invitation_query(context).where(OrgInvitation.id == invitation_id)
    )
    if row is None:
        raise authorization_error("not_found")
    if row.revoked_at is not None:
        return
    await record_invitation_revocation(mutation, context, row)
    await mutation.db.commit()


async def record_invitation_revocation(
    mutation: OrgMutationRequest, context: OrgControlContext, row: OrgInvitation
) -> None:
    row.revoked_at = datetime.now(UTC)
    bump_version(context.organization)
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=audit_actions.ORG_INVITATION_REVOKE,
        target_type="org_invitation",
        target_id=row.id,
        target_name_snapshot=row.email,
        outcome="success",
        request=mutation.request,
        metadata={"org_id": str(context.organization.id)},
    )
