"""Versioned changes to the organization's research search policy."""

from app.authz import audit_actions
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_write_control
from app.models.organization import Organization
from app.schemas.research_search import (
    RESEARCH_SEARCH_POLICY_KEY,
    ResearchSearchPolicy,
    ResearchSearchPolicyView,
)
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import bump_version, require_current_version, utc_timestamp


def research_search_policy_view(organization: Organization) -> ResearchSearchPolicyView:
    value = organization.settings.get(RESEARCH_SEARCH_POLICY_KEY)
    policy = ResearchSearchPolicy.model_validate(value) if value is not None else None
    return ResearchSearchPolicyView(
        policy=policy, updated_at=utc_timestamp(organization.updated_at)
    )


async def update_research_search_policy(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: ResearchSearchPolicy
) -> ResearchSearchPolicyView:
    context = await load_org_write_control(
        mutation.db,
        context.actor,
        OrgControlTarget(context.organization.id, "can_manage_policy"),
        context.organization.tenant_id,
    )
    organization = context.organization
    require_current_version(organization, mutation.expected_version)
    settings = payload.model_dump(mode="json")
    if organization.settings.get(RESEARCH_SEARCH_POLICY_KEY) == settings:
        return research_search_policy_view(organization)
    organization.settings = {**organization.settings, RESEARCH_SEARCH_POLICY_KEY: settings}
    bump_version(organization)
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=audit_actions.ORG_POLICY_UPDATE,
        target_type="organization",
        target_id=organization.id,
        target_name_snapshot=organization.name,
        outcome="success",
        request=mutation.request,
        tenant_id=organization.tenant_id,
        org_id=organization.id,
        metadata={
            "changed_fields": [RESEARCH_SEARCH_POLICY_KEY],
            "providers": list(payload.providers),
            "monthly_attempt_limit": payload.monthly_attempt_limit,
            "authz_epoch": organization.authz_epoch,
        },
    )
    view = research_search_policy_view(organization)
    await mutation.db.commit()
    return view
