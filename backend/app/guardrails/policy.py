"""Which rule set an agent run gets: company policy, agent binding, templates (plan §3.4).

Company policy (``tenants.settings["guardrails"]``):

* ``off``: no guardrail for the company's agents.
* ``open``: only agents that chose a template are guarded.
* ``default``: agents without a choice get the company default; an agent may pick another
  template or switch off, but locked rules of the default still apply.
* ``enforced``: every agent gets the company default and cannot switch it off.

In every mode an agent may switch off or change the action of a rule the company has not
locked. Without a company policy the installation default (``GUARDRAIL_DEFAULT_TEMPLATE``)
acts as ``default`` with no locks, or ``open`` when it is empty. Only standard agents are
guarded (plan §3.4.1). The result is pinned on the run (``run_snapshot``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.guardrails.approval import APPROVAL_PACKS
from app.guardrails.rules import ACTION_STRENGTH, Rule, RuleSet, builtin
from app.guardrails.run import UnknownGuardrailTemplateError
from app.guardrails.templates import BUILTIN_PREFIX, parse_ref, rules_from_json
from app.guardrails.tool_risk import (
    DEFAULT_ACTIONS,
    InvalidToolRiskError,
    ToolRiskPolicy,
    actions_from_json,
    policy_from_actions,
)
from app.models.guardrail_template import (
    AgentGuardrailBinding,
    GuardrailTemplate,
    GuardrailTemplateVersion,
)
from app.models.organization import Tenant

logger = logging.getLogger(__name__)

SETTINGS_KEY: Final = "guardrails"
MODES: Final = ("off", "open", "default", "enforced")


class GuardrailPolicyError(RuntimeError):
    """The company policy names a template that cannot be used; the run must not go unchecked."""


@dataclass(frozen=True, slots=True)
class CompanyPolicy:
    mode: str
    default_template: str | None = None
    locked_rules: frozenset[str] = frozenset()
    # Risk level → allow / approval / block (tool_risk.py); every level present.
    tool_risk: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_ACTIONS))


def policy_from_settings(values: dict[str, Any] | None) -> CompanyPolicy | None:
    raw = (values or {}).get(SETTINGS_KEY)
    if not isinstance(raw, dict) or raw.get("mode") not in MODES:
        return None
    locked = raw.get("locked_rules") or []
    try:
        tool_risk = actions_from_json(raw.get("tool_risk"))
    except InvalidToolRiskError:
        # Only the policy API writes this and it validates; anything else is unreadable.
        raise GuardrailPolicyError("the stored tool risk policy is not valid") from None
    return CompanyPolicy(
        raw["mode"],
        raw.get("default_template") or None,
        frozenset(str(rule) for rule in locked if isinstance(rule, str)),
        tool_risk,
    )


def policy_to_settings(policy: CompanyPolicy) -> dict[str, Any]:
    return {
        "mode": policy.mode,
        "default_template": policy.default_template,
        "locked_rules": sorted(policy.locked_rules),
        "tool_risk": dict(policy.tool_risk),
    }


def installation_policy() -> CompanyPolicy:
    name = settings.guardrail_default_template.strip()
    if not name:
        return CompanyPolicy("open")
    if builtin(name) is None:
        raise UnknownGuardrailTemplateError(f"unknown GUARDRAIL_DEFAULT_TEMPLATE {name!r}")
    return CompanyPolicy("default", f"{BUILTIN_PREFIX}{name}")


async def template_rules(db: AsyncSession, tenant_id: uuid.UUID, ref: str) -> RuleSet | None:
    """The rules a reference points at now: a built-in, or a company template's latest version."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    kind, key = parsed
    if kind == "builtin":
        found = builtin(key)
        # Named by its reference, like a company template, so runs and records say which.
        return replace(found, template=ref) if found else None
    template = await db.get(GuardrailTemplate, uuid.UUID(key))
    if (
        template is None
        or template.tenant_id != tenant_id
        or template.status != "published"
        or template.published_version is None
    ):
        return None
    version = await db.scalar(
        select(GuardrailTemplateVersion).where(
            GuardrailTemplateVersion.template_id == template.id,
            GuardrailTemplateVersion.version == template.published_version,
        )
    )
    if version is None:
        return None
    return RuleSet(ref, version.version, rules_from_json(version.rules))


def apply_overrides(
    rules: RuleSet, overrides: dict[str, Any] | None, locked: frozenset[str]
) -> RuleSet:
    """An agent's per-rule changes (off, another action) on every rule the company left open."""
    kept: list[Rule] = []
    for rule in rules.rules:
        change = (overrides or {}).get(rule.id)
        if rule.id in locked or not isinstance(change, dict):
            kept.append(rule)
            continue
        if change.get("enabled") is False:
            continue
        action = change.get("action")
        # The user's message stays as written: an input rule only blocks or warns.
        allowed = (
            action in ACTION_STRENGTH
            and ("input" not in rule.stages or action in ("block", "warn"))
            # Approval follows the template rule's constraints (approval.py).
            and (
                action != "approval"
                or (rule.stages == {"tool_args"} and set(rule.packs) <= APPROVAL_PACKS)
            )
        )
        kept.append(replace(rule, action=action) if allowed else rule)
    return replace(rules, rules=tuple(kept))


def _locked_only(rules: RuleSet, locked: frozenset[str]) -> RuleSet | None:
    kept = tuple(rule for rule in rules.rules if rule.id in locked)
    return replace(rules, rules=kept) if kept else None


async def resolve(
    db: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID | None, runtime_profile: str
) -> RuleSet | None:
    if runtime_profile != "standard":
        return None
    tenant = await db.get(Tenant, tenant_id)
    policy = policy_from_settings(tenant.settings if tenant else None) or installation_policy()
    if policy.mode == "off":
        return None
    binding = await db.get(AgentGuardrailBinding, agent_id) if agent_id else None
    default = await _default(db, tenant_id, policy)
    if policy.mode == "enforced" or binding is None or binding.mode == "inherit":
        chosen = default if policy.mode != "open" else None
    elif binding.mode == "off":
        # Off is the agent's choice except for what the company locked.
        return _locked_only(default, policy.locked_rules) if default else None
    else:
        chosen = await template_rules(db, tenant_id, binding.template_ref or "")
        if chosen is None:
            logger.warning("agent %s guardrail template unusable; using the default", agent_id)
            chosen = default
        elif default is not None:
            # The agent's template keeps the company's locked rules too.
            extra = tuple(r for r in default.rules if r.id in policy.locked_rules)
            names = {r.id for r in chosen.rules}
            chosen = replace(
                chosen, rules=chosen.rules + tuple(r for r in extra if r.id not in names)
            )
    if chosen is None:
        return None
    return apply_overrides(chosen, binding.rule_overrides if binding else None, policy.locked_rules)


async def _default(db: AsyncSession, tenant_id: uuid.UUID, policy: CompanyPolicy) -> RuleSet | None:
    if policy.default_template is None:
        if policy.mode == "enforced":
            raise GuardrailPolicyError("an enforced guardrail policy needs a default template")
        return None
    rules = await template_rules(db, tenant_id, policy.default_template)
    if rules is None:
        raise GuardrailPolicyError(f"guardrail template {policy.default_template!r} is not usable")
    return rules


async def resolve_tool_risk(
    db: AsyncSession, tenant_id: uuid.UUID, runtime_profile: str
) -> ToolRiskPolicy | None:
    """The company's tool risk actions for a run; ``None`` when all keep their default.

    Unlike detection rules this does not depend on the guardrail mode: approval and
    blocking of risky tools hold even when the company switched detection off.
    """
    if runtime_profile != "standard":
        return None
    tenant = await db.get(Tenant, tenant_id)
    saved = policy_from_settings(tenant.settings if tenant else None)
    return policy_from_actions(saved.tool_risk) if saved else None
