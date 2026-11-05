"""Which guardrail a run gets (until company policy and agent bindings: plan §6 step 4)."""

from __future__ import annotations

import uuid

from app.config import settings
from app.guardrails.middleware import Guard
from app.guardrails.rules import RuleSet, builtin


class UnknownGuardrailTemplateError(ValueError):
    """``GUARDRAIL_DEFAULT_TEMPLATE`` names no built-in template; the run must not go unchecked."""


def rules_for(runtime_profile: str) -> RuleSet | None:
    """The rule set for a run of ``runtime_profile``; ``None`` when guardrails are off.

    Only standard (user-built) agents: builder, assistant and other hidden profiles run
    Moldy's own prompts and are listed in the plan's uncovered-path table (§3.4.1).
    """
    name = settings.guardrail_default_template.strip()
    if not name or runtime_profile != "standard":
        return None
    rules = builtin(name)
    if rules is None:
        raise UnknownGuardrailTemplateError(f"unknown GUARDRAIL_DEFAULT_TEMPLATE {name!r}")
    return rules


def guard_for(runtime_profile: str, tenant_id: uuid.UUID | None) -> Guard | None:
    rules = rules_for(runtime_profile)
    return Guard(rules, tenant_id) if rules is not None else None
