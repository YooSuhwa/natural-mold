"""The old catalog PII middleware next to guardrails (guardrail-minimum-plan.md P1).

LangChain's ``PIIMiddleware`` (catalog type ``pii``) rewrites the stored history and
does not see streamed output. Where the run's guardrail rules already check the same
value before it is sent to the model, the old middleware is left out so the value is
handled once, by guardrails. Types guardrails do not detect (``mac_address``, ``url``),
and runs without guardrails, keep the old middleware as configured. The catalog marks the
type as superseded so new agents use guardrails instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app.guardrails.rules import RuleSet

LEGACY_PII_TYPE: Final = "pii"
# PIIMiddleware ``pii_type`` → the guardrail entity the common pack reports.
_ENTITIES: Final[Mapping[str, str]] = {"email": "EMAIL", "credit_card": "CARD", "ip": "IP"}


def covered_by_guardrails(config: Mapping[str, Any], rules: RuleSet | None) -> bool:
    """A ``pii`` middleware entry whose value the run's rules check before sending."""
    if config.get("type") != LEGACY_PII_TYPE or rules is None:
        return False
    params = config.get("params") or {}
    entity = _ENTITIES.get(str(params.get("pii_type") or "email"))
    if entity is None:
        return False
    return any(rule.matches(entity, "common", "llm_send") for rule in rules.rules)
