"""Company guardrail policy and templates (guardrail-minimum-plan.md §3.4).

``/api/tenants/{tenant_id}/guardrails``: company members read, ``can_manage_tenant``
writes; the policy shares the tenant ``updated_at`` ``If-Match`` with the other company
policies, and template writes check the template's own ``updated_at``. Templates are drafts
until published; a published version never changes.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import GOVERNANCE, SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.tenant_policies import company_context
from app.schemas.guardrails import (
    GuardrailPolicyOut,
    GuardrailPolicyUpdate,
    GuardrailTemplateCreate,
    GuardrailTemplateOut,
    GuardrailTemplateRevert,
    GuardrailTemplateUpdate,
    GuardrailTemplateVersionOut,
    RegistrationSettingsOut,
    RegistrationSettingsUpdate,
    ToolPolicyImpactOut,
    ToolPolicyListOut,
    ToolPolicySaved,
    ToolPolicyTarget,
    ToolPolicyUpdate,
)
from app.services import guardrails_admin, registration_admin, tool_policies_admin
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}/guardrails", tags=["company administration"])


def _mutation(
    db: AsyncSession, request: Request, user: CurrentUser, if_match: str | None = None
) -> guardrails_admin.CompanyMutation:
    return guardrails_admin.CompanyMutation(db, request, user, if_match)


@router.get("", response_model=GuardrailPolicyOut)
@authz_policy(SELF)
async def get_guardrail_policy(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> JSONResponse:
    result = await guardrails_admin.policy_view(db, context.tenant)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.put("", response_model=GuardrailPolicyOut)
@authz_policy(GOVERNANCE)
async def put_guardrail_policy(
    payload: GuardrailPolicyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    result = await guardrails_admin.update_policy(
        _mutation(db, request, user, if_match), context, payload
    )
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.get("/templates", response_model=list[GuardrailTemplateOut])
@authz_policy(SELF)
async def list_guardrail_templates(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> list[GuardrailTemplateOut]:
    return await guardrails_admin.list_templates(db, context.tenant.id)


@router.post("/templates", response_model=GuardrailTemplateOut, status_code=201)
@authz_policy(GOVERNANCE)
async def create_guardrail_template(
    payload: GuardrailTemplateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
) -> GuardrailTemplateOut:
    return await guardrails_admin.create_template(_mutation(db, request, user), context, payload)


@router.patch("/templates/{template_id}", response_model=GuardrailTemplateOut)
@authz_policy(GOVERNANCE)
async def update_guardrail_template(
    template_id: UUID,
    payload: GuardrailTemplateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> GuardrailTemplateOut:
    return await guardrails_admin.update_template(
        _mutation(db, request, user, if_match), context, template_id, payload
    )


@router.post("/templates/{template_id}/publish", response_model=GuardrailTemplateOut)
@authz_policy(GOVERNANCE)
async def publish_guardrail_template(
    template_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> GuardrailTemplateOut:
    return await guardrails_admin.publish_template(
        _mutation(db, request, user, if_match), context, template_id
    )


@router.post("/templates/{template_id}/archive", response_model=GuardrailTemplateOut)
@authz_policy(GOVERNANCE)
async def archive_guardrail_template(
    template_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> GuardrailTemplateOut:
    return await guardrails_admin.archive_template(
        _mutation(db, request, user, if_match), context, template_id
    )


@router.get("/templates/{template_id}/versions", response_model=list[GuardrailTemplateVersionOut])
@authz_policy(SELF)
async def list_guardrail_template_versions(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> list[GuardrailTemplateVersionOut]:
    return await guardrails_admin.list_versions(db, context.tenant.id, template_id)


@router.post("/templates/{template_id}/revert", response_model=GuardrailTemplateOut)
@authz_policy(GOVERNANCE)
async def revert_guardrail_template(
    template_id: UUID,
    payload: GuardrailTemplateRevert,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> GuardrailTemplateOut:
    return await guardrails_admin.revert_template(
        _mutation(db, request, user, if_match), context, template_id, payload
    )


# --- company policy per tool (tool-governance-plan 3.4) ---------------------------------


@router.get("/tool-policies", response_model=ToolPolicyListOut)
@authz_policy(SELF)
async def list_tool_policies(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> ToolPolicyListOut:
    return ToolPolicyListOut(
        policies=await tool_policies_admin.list_policies(db, context.tenant.id)
    )


@router.get("/tool-policies/targets", response_model=list[ToolPolicyTarget])
@authz_policy(GOVERNANCE)
async def list_tool_policy_targets(
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(company_context),
) -> list[ToolPolicyTarget]:
    return await tool_policies_admin.targets(db, context)


@router.put("/tool-policies/{resource_key}", response_model=ToolPolicySaved)
@authz_policy(GOVERNANCE)
async def put_tool_policy(
    resource_key: str,
    payload: ToolPolicyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
) -> ToolPolicySaved:
    return await tool_policies_admin.save(
        _mutation(db, request, user), context, resource_key, payload
    )


@router.post("/tool-policies/{resource_key}/impact", response_model=ToolPolicyImpactOut)
@authz_policy(GOVERNANCE)
async def preview_tool_policy_impact(
    resource_key: str,
    payload: ToolPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
) -> ToolPolicyImpactOut:
    return await tool_policies_admin.impact(db, context, resource_key, payload)


# --- who may register tools, MCP servers, skills, agents (tool-governance-plan 3.6) -----


@router.get("/registration", response_model=RegistrationSettingsOut)
@authz_policy(SELF)
async def get_registration_settings(
    context: TenantAdminContext = Depends(company_context),
) -> JSONResponse:
    result = registration_admin.view(context.tenant)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.put("/registration", response_model=RegistrationSettingsOut)
@authz_policy(GOVERNANCE)
async def put_registration_settings(
    payload: RegistrationSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    result = await registration_admin.update(
        _mutation(db, request, user, if_match), context, payload
    )
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
