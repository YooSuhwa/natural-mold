"""Company guardrail policy, templates and agent bindings (plan §3.4).

Company writes need ``can_manage_tenant``; the policy shares the tenant's ``updated_at``
optimistic lock with the other company policies. Agent bindings need ``agent#can_edit``.
Every change is audited with before/after values (plan §3.5, F2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.dependencies import CurrentUser
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.guardrails import policy as guardrail_policy
from app.guardrails.rules import BUILTIN_TEMPLATES, RuleSet
from app.guardrails.templates import (
    BUILTIN_PREFIX,
    InvalidRulesError,
    company_ref,
    rule_to_json,
    rules_from_json,
)
from app.guardrails.tool_risk import DEFAULT_ACTIONS, actions_from_json
from app.models.agent import Agent
from app.models.guardrail_template import (
    AgentGuardrailBinding,
    GuardrailTemplate,
    GuardrailTemplateVersion,
)
from app.models.organization import Tenant
from app.schemas.guardrails import (
    AgentGuardrailOut,
    AgentGuardrailUpdate,
    GuardrailPolicyOut,
    GuardrailPolicyUpdate,
    GuardrailRuleOut,
    GuardrailRuleOverride,
    GuardrailTemplateChoice,
    GuardrailTemplateCreate,
    GuardrailTemplateOut,
    GuardrailTemplateUpdate,
)
from app.services import audit_service
from app.services.tenants.versioning import bump_timestamp, require_current_version
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

POLICY_UPDATE = "guardrail.policy_update"
TEMPLATE_CREATE = "guardrail.template_create"
TEMPLATE_UPDATE = "guardrail.template_update"
TEMPLATE_PUBLISH = "guardrail.template_publish"
TEMPLATE_ARCHIVE = "guardrail.template_archive"
BINDING_UPDATE = "guardrail.binding_update"


@dataclass(frozen=True, slots=True)
class CompanyMutation:
    db: AsyncSession
    request: Request
    actor: CurrentUser
    expected_version: str | None = None


def _rules_out(rules: RuleSet) -> list[GuardrailRuleOut]:
    return [GuardrailRuleOut(**rule_to_json(rule)) for rule in rules.rules]


def _choice(rules: RuleSet, name: str) -> GuardrailTemplateChoice:
    kind = "builtin" if rules.template.startswith(BUILTIN_PREFIX) else "company"
    return GuardrailTemplateChoice(
        ref=rules.template, name=name, kind=kind, version=rules.version, rules=_rules_out(rules)
    )


async def template_choices(db: AsyncSession, tenant_id: uuid.UUID) -> list[GuardrailTemplateChoice]:
    """Built-in templates, then the company's published ones."""
    choices = [
        _choice(
            RuleSet(f"{BUILTIN_PREFIX}{name}", rules.version, rules.rules),
            name,
        )
        for name, rules in BUILTIN_TEMPLATES.items()
    ]
    templates = await db.scalars(
        select(GuardrailTemplate)
        .where(GuardrailTemplate.tenant_id == tenant_id, GuardrailTemplate.status == "published")
        .order_by(GuardrailTemplate.name)
    )
    for template in templates:
        rules = await guardrail_policy.template_rules(db, tenant_id, company_ref(template.id))
        if rules is not None:
            choices.append(_choice(rules, template.name))
    return choices


async def _audit(
    db: AsyncSession,
    request: Request | None,
    actor: CurrentUser,
    tenant_id: uuid.UUID,
    action: str,
    target: tuple[str, Any, str | None],
    before: Any,
    after: Any,
) -> None:
    target_type, target_id, target_name = target
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.id,
        actor_email_snapshot=actor.email,
        owner_user_id=actor.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name_snapshot=target_name,
        outcome="success",
        request=request,
        tenant_id=tenant_id,
        metadata={"before": before, "after": after},
    )


# --- company policy ---------------------------------------------------------------------


def _policy_snapshot(tenant: Tenant) -> dict[str, Any] | None:
    saved = guardrail_policy.policy_from_settings(tenant.settings)
    return guardrail_policy.policy_to_settings(saved) if saved else None


async def policy_view(db: AsyncSession, tenant: Tenant) -> GuardrailPolicyOut:
    saved = guardrail_policy.policy_from_settings(tenant.settings)
    effective = saved or guardrail_policy.installation_policy()
    return GuardrailPolicyOut(
        mode=effective.mode,  # type: ignore[arg-type]
        default_template=effective.default_template,
        locked_rules=sorted(effective.locked_rules),
        tool_risk=dict(effective.tool_risk),  # type: ignore[arg-type]
        saved=saved is not None,
        templates=await template_choices(db, tenant.id),
        updated_at=tenant.updated_at,
    )


async def update_policy(
    mutation: CompanyMutation, context: TenantAdminContext, payload: GuardrailPolicyUpdate
) -> GuardrailPolicyOut:
    db = mutation.db
    fresh = await load_tenant_admin_context(
        db, context.actor, TenantAdminTarget(context.tenant.id, permission="can_manage_tenant")
    )
    tenant = fresh.tenant
    require_current_version(tenant.updated_at, mutation.expected_version)
    default = None
    if payload.default_template is not None:
        default = await guardrail_policy.template_rules(db, tenant.id, payload.default_template)
        if default is None:
            raise ValidationError("GUARDRAIL_TEMPLATE_UNUSABLE", payload.default_template)
    if payload.mode in ("default", "enforced") and default is None:
        raise ValidationError("GUARDRAIL_DEFAULT_TEMPLATE_REQUIRED", payload.mode)
    known = {rule.id for rule in default.rules} if default else set()
    if unknown := sorted(set(payload.locked_rules) - known):
        raise ValidationError("GUARDRAIL_LOCKED_RULE_UNKNOWN", ", ".join(unknown))
    before = _policy_snapshot(tenant)
    previous = guardrail_policy.policy_from_settings(tenant.settings)
    tool_risk = (
        actions_from_json(payload.tool_risk)
        if payload.tool_risk is not None
        else dict(previous.tool_risk if previous else DEFAULT_ACTIONS)
    )
    policy = guardrail_policy.CompanyPolicy(
        payload.mode, payload.default_template, frozenset(payload.locked_rules), tool_risk
    )
    tenant.settings = {
        **(tenant.settings or {}),
        guardrail_policy.SETTINGS_KEY: guardrail_policy.policy_to_settings(policy),
    }
    tenant.updated_at = bump_timestamp(tenant.updated_at)
    await _audit(
        db,
        mutation.request,
        mutation.actor,
        tenant.id,
        POLICY_UPDATE,
        ("tenant", tenant.id, tenant.name),
        before,
        _policy_snapshot(tenant),
    )
    result = await policy_view(db, tenant)
    await db.commit()
    return result


# --- company templates ------------------------------------------------------------------


def _template_out(template: GuardrailTemplate) -> GuardrailTemplateOut:
    return GuardrailTemplateOut(
        id=template.id,
        ref=company_ref(template.id),
        name=template.name,
        description=template.description,
        status=template.status,  # type: ignore[arg-type]
        draft_rules=template.draft_rules,
        published_version=template.published_version,
        updated_at=template.updated_at,
    )


def _validated(rules: Any) -> list[dict[str, Any]]:
    try:
        return [rule_to_json(rule) for rule in rules_from_json(rules)]
    except InvalidRulesError as exc:
        raise ValidationError("GUARDRAIL_RULES_INVALID", str(exc)) from exc


async def _template(
    db: AsyncSession, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> GuardrailTemplate:
    template = await db.get(GuardrailTemplate, template_id)
    if template is None or template.tenant_id != tenant_id:
        raise NotFoundError("GUARDRAIL_TEMPLATE_NOT_FOUND", "가드레일 템플릿을 찾을 수 없습니다")
    return template


async def _company_admin(mutation: CompanyMutation, context: TenantAdminContext) -> Tenant:
    fresh = await load_tenant_admin_context(
        mutation.db,
        context.actor,
        TenantAdminTarget(context.tenant.id, permission="can_manage_tenant"),
    )
    return fresh.tenant


async def list_templates(db: AsyncSession, tenant_id: uuid.UUID) -> list[GuardrailTemplateOut]:
    rows = await db.scalars(
        select(GuardrailTemplate)
        .where(GuardrailTemplate.tenant_id == tenant_id)
        .order_by(GuardrailTemplate.name, GuardrailTemplate.id)
    )
    return [_template_out(row) for row in rows]


async def create_template(
    mutation: CompanyMutation, context: TenantAdminContext, payload: GuardrailTemplateCreate
) -> GuardrailTemplateOut:
    db = mutation.db
    tenant = await _company_admin(mutation, context)
    if payload.copy_from is not None:
        source = await guardrail_policy.template_rules(db, tenant.id, payload.copy_from)
        if source is None:
            raise ValidationError("GUARDRAIL_TEMPLATE_UNUSABLE", payload.copy_from)
        rules = [rule_to_json(rule) for rule in source.rules]
    else:
        rules = _validated(payload.rules)
    template = GuardrailTemplate(
        tenant_id=tenant.id,
        name=payload.name,
        description=payload.description,
        status="draft",
        draft_rules=rules,
        created_by=mutation.actor.id,
    )
    db.add(template)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("GUARDRAIL_TEMPLATE_NAME_TAKEN", payload.name) from exc
    await _audit(
        db,
        mutation.request,
        mutation.actor,
        tenant.id,
        TEMPLATE_CREATE,
        ("guardrail_template", template.id, template.name),
        None,
        {"name": template.name, "copy_from": payload.copy_from, "rules": len(rules)},
    )
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


async def update_template(
    mutation: CompanyMutation,
    context: TenantAdminContext,
    template_id: uuid.UUID,
    payload: GuardrailTemplateUpdate,
) -> GuardrailTemplateOut:
    db = mutation.db
    tenant = await _company_admin(mutation, context)
    template = await _template(db, tenant.id, template_id)
    if template.status == "archived":
        raise ConflictError("GUARDRAIL_TEMPLATE_ARCHIVED", template.name)
    before = {
        "name": template.name,
        "description": template.description,
        "rules": template.draft_rules,
    }
    if payload.name is not None:
        template.name = payload.name
    if payload.description is not None:
        template.description = payload.description
    if payload.rules is not None:
        template.draft_rules = _validated(payload.rules)
    after = {
        "name": template.name,
        "description": template.description,
        "rules": template.draft_rules,
    }
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("GUARDRAIL_TEMPLATE_NAME_TAKEN", payload.name or "") from exc
    await _audit(
        db,
        mutation.request,
        mutation.actor,
        tenant.id,
        TEMPLATE_UPDATE,
        ("guardrail_template", template.id, template.name),
        before,
        after,
    )
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


async def publish_template(
    mutation: CompanyMutation, context: TenantAdminContext, template_id: uuid.UUID
) -> GuardrailTemplateOut:
    """Freeze the draft as the next version; runs already started keep their pinned rules."""
    db = mutation.db
    tenant = await _company_admin(mutation, context)
    template = await _template(db, tenant.id, template_id)
    if template.status == "archived":
        raise ConflictError("GUARDRAIL_TEMPLATE_ARCHIVED", template.name)
    rules = _validated(template.draft_rules)
    version = (template.published_version or 0) + 1
    db.add(
        GuardrailTemplateVersion(
            tenant_id=tenant.id,
            template_id=template.id,
            version=version,
            rules=rules,
            published_by=mutation.actor.id,
        )
    )
    before = template.published_version
    template.published_version = version
    template.status = "published"
    await _audit(
        db,
        mutation.request,
        mutation.actor,
        tenant.id,
        TEMPLATE_PUBLISH,
        ("guardrail_template", template.id, template.name),
        {"version": before},
        {"version": version, "rules": rules},
    )
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


async def archive_template(
    mutation: CompanyMutation, context: TenantAdminContext, template_id: uuid.UUID
) -> GuardrailTemplateOut:
    db = mutation.db
    tenant = await _company_admin(mutation, context)
    template = await _template(db, tenant.id, template_id)
    policy = guardrail_policy.policy_from_settings(tenant.settings)
    if policy is not None and policy.default_template == company_ref(template.id):
        raise ConflictError("GUARDRAIL_TEMPLATE_IN_USE", template.name)
    before = template.status
    template.status = "archived"
    await _audit(
        db,
        mutation.request,
        mutation.actor,
        tenant.id,
        TEMPLATE_ARCHIVE,
        ("guardrail_template", template.id, template.name),
        {"status": before},
        {"status": "archived"},
    )
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


# --- agent bindings ---------------------------------------------------------------------


async def _agent(db: AsyncSession, agent_id: uuid.UUID, scope: OrgContext) -> Agent:
    agent = await db.scalar(
        select(Agent).where(Agent.id == agent_id, private_scope_predicate(Agent, scope))
    )
    if agent is None:
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
    return agent


def _binding_snapshot(binding: AgentGuardrailBinding | None) -> dict[str, Any]:
    if binding is None:
        return {"mode": "inherit", "template_ref": None, "rule_overrides": {}}
    return {
        "mode": binding.mode,
        "template_ref": binding.template_ref,
        "rule_overrides": binding.rule_overrides or {},
    }


async def binding_view(
    db: AsyncSession, agent_id: uuid.UUID, scope: OrgContext
) -> AgentGuardrailOut:
    agent = await _agent(db, agent_id, scope)
    return await _binding_out(db, agent)


async def _binding_out(db: AsyncSession, agent: Agent) -> AgentGuardrailOut:
    tenant = await db.get(Tenant, agent.tenant_id)
    policy = (
        guardrail_policy.policy_from_settings(tenant.settings if tenant else None)
        or guardrail_policy.installation_policy()
    )
    binding = _binding_snapshot(await db.get(AgentGuardrailBinding, agent.id))
    effective = await guardrail_policy.resolve(db, agent.tenant_id, agent.id, agent.runtime_profile)
    choices = await template_choices(db, agent.tenant_id)
    # Named like the choice it came from, not by its reference.
    effective_name = next(
        (item.name for item in choices if effective and item.ref == effective.template),
        effective.template if effective else "",
    )
    return AgentGuardrailOut(
        mode=binding["mode"],
        template_ref=binding["template_ref"],
        rule_overrides={
            key: GuardrailRuleOverride(**value)
            for key, value in binding["rule_overrides"].items()
            if isinstance(value, dict)
        },
        policy_mode=policy.mode,  # type: ignore[arg-type]
        policy_default_template=policy.default_template,
        locked_rules=sorted(policy.locked_rules),
        tool_risk=dict(policy.tool_risk),  # type: ignore[arg-type]
        effective=_choice(effective, effective_name) if effective else None,
        templates=choices,
    )


async def update_binding(
    db: AsyncSession,
    agent_id: uuid.UUID,
    scope: OrgContext,
    payload: AgentGuardrailUpdate,
    *,
    actor: CurrentUser,
    request: Request | None,
) -> AgentGuardrailOut:
    agent = await _agent(db, agent_id, scope)
    tenant = await db.get(Tenant, agent.tenant_id)
    policy = (
        guardrail_policy.policy_from_settings(tenant.settings if tenant else None)
        or guardrail_policy.installation_policy()
    )
    if policy.mode == "enforced" and payload.mode != "inherit":
        raise ConflictError("GUARDRAIL_POLICY_ENFORCED", "회사 정책이 템플릿을 고정합니다")
    if payload.mode == "template":
        usable = payload.template_ref and await guardrail_policy.template_rules(
            db, agent.tenant_id, payload.template_ref
        )
        if not usable:
            raise ValidationError("GUARDRAIL_TEMPLATE_UNUSABLE", payload.template_ref or "")
    if locked := sorted(set(payload.rule_overrides) & policy.locked_rules):
        raise ConflictError("GUARDRAIL_RULE_LOCKED", ", ".join(locked))
    binding = await db.get(AgentGuardrailBinding, agent.id)
    before = _binding_snapshot(binding)
    if binding is None:
        binding = AgentGuardrailBinding(agent_id=agent.id, tenant_id=agent.tenant_id)
        db.add(binding)
    binding.mode = payload.mode
    binding.template_ref = payload.template_ref if payload.mode == "template" else None
    binding.rule_overrides = {
        key: value.model_dump() for key, value in payload.rule_overrides.items()
    }
    binding.updated_by = actor.id
    await db.flush()
    await _audit(
        db,
        request,
        actor,
        agent.tenant_id,
        BINDING_UPDATE,
        ("agent", agent.id, agent.name),
        before,
        _binding_snapshot(binding),
    )
    result = await _binding_out(db, agent)
    await db.commit()
    return result
