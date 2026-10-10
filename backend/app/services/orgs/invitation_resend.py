"""An explicit resend invalidates older links without storing plaintext tokens."""

from datetime import UTC, datetime
from uuid import UUID

from app.authz.error_codes import authorization_error
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_invitation_queries import invitation_query
from app.models.organization_group import OrgInvitation
from app.schemas.org_invitations import InvitationCreate, InvitationCreated
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitations import (
    create_invitation,
    creation_view,
    record_invitation_revocation,
)
from app.services.orgs.versioning import utc_timestamp


async def resend_invitation(
    mutation: OrgMutationRequest, context: OrgControlContext, invitation_id: UUID
) -> tuple[InvitationCreated, bool]:
    context = await load_org_control(
        mutation.db, context.actor, OrgControlTarget(context.organization.id, "can_manage_members")
    )
    original = await mutation.db.scalar(
        invitation_query(context).where(OrgInvitation.id == invitation_id)
    )
    if original is None:
        raise authorization_error("not_found")
    if original.accepted_at is not None:
        raise authorization_error("invitation_used")
    pending = list(
        (
            await mutation.db.scalars(
                invitation_query(context)
                .where(
                    OrgInvitation.email == original.email,
                    OrgInvitation.accepted_at.is_(None),
                    OrgInvitation.revoked_at.is_(None),
                    OrgInvitation.expires_at > datetime.now(UTC),
                )
                .order_by(OrgInvitation.created_at.desc(), OrgInvitation.id)
            )
        ).all()
    )
    # A repeated request for the same original row may copy its newer replacement.
    if original.revoked_at is not None:
        for candidate in pending:
            if (
                candidate.role == original.role
                and candidate.group_ids == original.group_ids
                and utc_timestamp(candidate.created_at) >= utc_timestamp(original.revoked_at)
            ):
                view = creation_view(candidate)
                if view.token is not None:
                    return view, False
    for row in pending:
        await record_invitation_revocation(mutation, context, row)
    if original.revoked_at is None:
        await record_invitation_revocation(mutation, context, original)
    payload = InvitationCreate.model_validate(
        {
            "email": original.email,
            "role": original.role,
            "group_ids": original.group_ids,
        }
    )
    # Creation commits the revocations, new hash, version and both audit actions together.
    result, created = await create_invitation(mutation, context, payload)
    if not created:
        await mutation.db.commit()
    return result, created
