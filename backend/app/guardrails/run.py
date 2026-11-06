"""Which guardrail a run gets.

Runs whose entry resolved the company policy carry the result on ``AgentConfig``
(``guardrail_resolved``; ``policy.resolve``, pinned on the run). Anything else falls back
to the installation default (``GUARDRAIL_DEFAULT_TEMPLATE``) so it is never less guarded.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.config import settings
from app.guardrails.events import RunScope
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


def effective_rules(cfg: Any) -> RuleSet | None:
    """The run's rule set: the resolved company policy, else the installation default."""
    if getattr(cfg, "guardrail_resolved", False):
        return cfg.guardrail_rules
    return rules_for(cfg.runtime_profile)


def guard_for(
    rules: RuleSet | None, tenant_id: uuid.UUID | None, scope: RunScope | None = None
) -> Guard | None:
    return Guard(rules, tenant_id, scope) if rules is not None else None
