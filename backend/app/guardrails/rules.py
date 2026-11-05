"""Guardrail rule sets: what the middleware checks and does (guardrail-minimum-plan.md §3.3.2).

A rule is detector packs + entities + stages + an action. The middleware takes a resolved
``RuleSet`` and nothing else, so templates, company policy and agent bindings (step 4) only
have to produce one. Until then the set comes from a built-in template named by
``GUARDRAIL_DEFAULT_TEMPLATE`` (empty: guardrails off).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Stage = Literal["llm_send", "tool_args", "tool_result"]
Action = Literal["block", "redact", "hash", "mask", "warn"]

# The strongest action wins when rules overlap on one span.
ACTION_STRENGTH: dict[Action, int] = {"warn": 0, "mask": 1, "hash": 2, "redact": 3, "block": 4}
# A rule naming ``kr_model`` also accepts what its rules fallback found (model_pack).
PACK_FAMILIES: dict[str, frozenset[str]] = {"kr_model": frozenset({"kr_model", "kr"})}


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    packs: tuple[str, ...]
    stages: frozenset[Stage]
    action: Action
    # None: every entity the packs report.
    entities: frozenset[str] | None = None

    def matches(self, entity: str, pack: str, stage: Stage) -> bool:
        if stage not in self.stages:
            return False
        if self.entities is not None and entity not in self.entities:
            return False
        return any(pack in PACK_FAMILIES.get(name, frozenset({name})) for name in self.packs)


@dataclass(frozen=True, slots=True)
class RuleSet:
    template: str
    version: int
    rules: tuple[Rule, ...]

    def packs_for(self, stage: Stage) -> tuple[str, ...]:
        names = {name for rule in self.rules if stage in rule.stages for name in rule.packs}
        return tuple(sorted(names))

    def rule_for(self, entity: str, pack: str, stage: Stage) -> Rule | None:
        """The strongest matching rule; ties go to the earlier one."""
        best: Rule | None = None
        for rule in self.rules:
            if rule.matches(entity, pack, stage) and (
                best is None or ACTION_STRENGTH[rule.action] > ACTION_STRENGTH[best.action]
            ):
                best = rule
        return best


_SEND: frozenset[Stage] = frozenset({"llm_send"})
_ALL: frozenset[Stage] = frozenset({"llm_send", "tool_args", "tool_result"})
_IDS = frozenset(
    {"RRN", "FRN", "PASSPORT", "DRIVER_LICENSE", "CARD", "ACCOUNT", "MEDICAL_INSURANCE"}
)

BUILTIN_TEMPLATES: dict[str, RuleSet] = {
    # Secrets never leave; identifiers are replaced before a model or a tool result sees
    # them; contacts keep their last digits; names and addresses are recorded only until
    # pseudonymization (plan §3.3.1) can keep their meaning.
    "kr-default": RuleSet(
        "kr-default",
        1,
        (
            Rule("secrets", ("common",), _ALL, "block", frozenset({"SECRET"})),
            Rule("kr-identifiers", ("kr_model",), _ALL, "redact", _IDS),
            Rule("contacts", ("common", "kr_model"), _SEND, "mask", frozenset({"PHONE", "EMAIL"})),
            Rule("people", ("kr_model",), _SEND, "warn", frozenset({"PERSON", "ADDRESS"})),
        ),
    ),
    # Observation mode: every finding recorded, nothing changed.
    "kr-observe": RuleSet(
        "kr-observe", 1, (Rule("observe", ("common", "kr_model"), _ALL, "warn"),)
    ),
}


def builtin(name: str) -> RuleSet | None:
    return BUILTIN_TEMPLATES.get(name) if name else None
