"""Assignment commits share one permission/version boundary and audit format."""

from dataclasses import dataclass
from typing import Literal

from app.authz.org_control import OrgControlContext
from app.schemas.org_assignments import Principal
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import bump_version


@dataclass(frozen=True, slots=True)
class AssignmentWrite:
    action: Literal[
        "org.role_grant", "org.role_revoke", "org.capability_grant", "org.capability_revoke"
    ]
    principal: Principal
    values: tuple[str, ...]


async def finish_assignment_write(
    mutation: OrgMutationRequest, context: OrgControlContext, changes: tuple[AssignmentWrite, ...]
) -> None:
    bump_version(context.organization)
    for change in changes:
        if not change.values:
            continue
        await record_assignment_event(mutation, context, change)
    await mutation.db.commit()


async def record_assignment_event(
    mutation: OrgMutationRequest, context: OrgControlContext, change: AssignmentWrite
) -> None:
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=change.action,
        target_type="organization",
        target_id=context.organization.id,
        target_name_snapshot=context.organization.name,
        outcome="success",
        request=mutation.request,
        metadata={
            "principal_type": change.principal.type,
            "principal_id": str(change.principal.id),
            "values": list(change.values),
            "authz_epoch": context.organization.authz_epoch,
        },
    )
