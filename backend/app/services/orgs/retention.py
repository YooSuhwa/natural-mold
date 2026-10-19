"""``GET|PUT /api/orgs/{org_id}/retention`` (plan 9.7, 10.7, S08; E-08).

Reading needs organization membership; writing ``can_manage_policy`` with the
organization version as ``If-Match``. ``PUT`` replaces the whole policy. A change that
shortens any period is not saved without ``confirm=true``: the response carries
``saved=false`` and the counts the next sweep would delete under the submitted policy.
"""

from datetime import UTC, datetime

from app.authz import audit_actions
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.models.organization import Organization
from app.schemas.retention import (
    ORG_SETTING,
    OrgRetention,
    OrgRetentionUpdate,
    OrgRetentionView,
    OrgRetentionWrite,
    org_retention,
    shorter,
)
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import bump_version, require_current_version, utc_timestamp
from app.services.retention.targets import OrgIds, preview_org


def org_retention_view(organization: Organization) -> OrgRetentionView:
    policy = org_retention(organization.settings)
    return OrgRetentionView(
        **policy.model_dump(), updated_at=utc_timestamp(organization.updated_at)
    )


def shortens(after: OrgRetention, before: OrgRetention) -> bool:
    return (
        shorter(after.conversation_days, before.conversation_days)
        or shorter(after.agent_trash_days, before.agent_trash_days)
        or shorter(after.removed_member_days, before.removed_member_days)
    )


async def update_org_retention(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: OrgRetentionUpdate
) -> OrgRetentionWrite:
    db = mutation.db
    context = await load_org_control(
        db, context.actor, OrgControlTarget(context.organization.id, "can_manage_policy")
    )
    organization = context.organization
    require_current_version(organization, mutation.expected_version)
    before = org_retention(organization.settings)
    after = OrgRetention.model_validate(payload.model_dump(exclude={"confirm"}))
    if after == before:
        return OrgRetentionWrite(saved=True, policy=org_retention_view(organization))
    if shortens(after, before) and not payload.confirm:
        scope = OrgIds(organization.id, organization.tenant_id)
        preview = await preview_org(db, scope, after, datetime.now(UTC))
        view = org_retention_view(organization)
        await db.rollback()  # release the organization row lock; nothing was written
        return OrgRetentionWrite(saved=False, preview=preview, policy=view)
    organization.settings = {**(organization.settings or {}), ORG_SETTING: after.as_setting()}
    bump_version(organization)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=audit_actions.POLICY_RETENTION_UPDATE,
        target_type="organization",
        target_id=organization.id,
        target_name_snapshot=organization.name,
        outcome="success",
        request=mutation.request,
        tenant_id=organization.tenant_id,
        metadata={"before": before.as_setting(), "after": after.as_setting()},
    )
    view = org_retention_view(organization)
    await db.commit()
    return OrgRetentionWrite(saved=True, policy=view)
