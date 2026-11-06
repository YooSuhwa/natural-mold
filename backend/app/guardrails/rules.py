"""Guardrail rule sets: what the middleware checks and does (guardrail-minimum-plan.md §3.3.2).

A rule is detector packs + entities + stages + an action. The middleware takes a resolved
``RuleSet`` and nothing else, so templates, company policy and agent bindings (step 4) only
have to produce one. Until then the set comes from a built-in template named by
``GUARDRAIL_DEFAULT_TEMPLATE`` (empty: guardrails off).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# output: what the model answers, streamed to the user and stored (plan stage ⑤).
# ``input``: the user's new message, before the model (①; block or warn only).
Stage = Literal["input", "llm_send", "tool_args", "tool_result", "output"]
Action = Literal["block", "redact", "hash", "pseudonymize", "mask", "warn"]

# The strongest action wins when rules overlap on one span.
ACTION_STRENGTH: dict[Action, int] = {
    "warn": 0,
    "mask": 1,
    "pseudonymize": 2,
    "hash": 3,
    "redact": 4,
    "block": 5,
}
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
    # pseudonymize only: tools get the original value back (a mail tool needs the real
    # name); False leaves the pseudonym in their arguments (plan §3.3.1).
    restore_for_tools: bool = False
    # pseudonymize only: "tag" (<<PERSON_1>>, default) or "natural" (a made-up Korean name
    # with the same final consonant, so particles stay right; other entities stay tags).
    pseudonym_style: Literal["tag", "natural"] = "tag"

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
_SEND_OUT: frozenset[Stage] = frozenset({"llm_send", "output"})
_IN: frozenset[Stage] = frozenset({"llm_send", "tool_args", "tool_result"})
_OUT: frozenset[Stage] = frozenset({"output"})
_ALL: frozenset[Stage] = _IN | _OUT
_INPUT: frozenset[Stage] = frozenset({"input"})
_IDS = frozenset(
    {"RRN", "FRN", "PASSPORT", "DRIVER_LICENSE", "CARD", "ACCOUNT", "MEDICAL_INSURANCE"}
)

BUILTIN_TEMPLATES: dict[str, RuleSet] = {
    # Secrets never leave and are replaced if the model writes one; identifiers are replaced
    # before a model, a tool result or the user sees them; contacts keep their last digits;
    # names and addresses reach the model as pseudonyms and come back as the real values
    # in answers and tool arguments (plan §3.3.1).
    "kr-default": RuleSet(
        "kr-default",
        4,
        (
            Rule("jailbreak", ("jailbreak",), _INPUT, "warn"),
            Rule("secrets", ("common",), _IN, "block", frozenset({"SECRET"})),
            Rule("secrets-output", ("common",), _OUT, "redact", frozenset({"SECRET"})),
            Rule("kr-identifiers", ("kr_model",), _ALL, "redact", _IDS),
            Rule(
                "contacts", ("common", "kr_model"), _SEND_OUT, "mask", frozenset({"PHONE", "EMAIL"})
            ),
            Rule(
                "people",
                ("kr_model",),
                _SEND,
                "pseudonymize",
                frozenset({"PERSON", "ADDRESS"}),
                restore_for_tools=True,
            ),
            Rule("people-output", ("kr_model",), _OUT, "warn", frozenset({"PERSON", "ADDRESS"})),
        ),
    ),
    # kr-default with natural pseudonyms for people (plan §3.3.1).
    "kr-natural": RuleSet(
        "kr-natural",
        2,
        (
            Rule("jailbreak", ("jailbreak",), _INPUT, "warn"),
            Rule("secrets", ("common",), _IN, "block", frozenset({"SECRET"})),
            Rule("secrets-output", ("common",), _OUT, "redact", frozenset({"SECRET"})),
            Rule("kr-identifiers", ("kr_model",), _ALL, "redact", _IDS),
            Rule(
                "contacts", ("common", "kr_model"), _SEND_OUT, "mask", frozenset({"PHONE", "EMAIL"})
            ),
            Rule(
                "people",
                ("kr_model",),
                _SEND,
                "pseudonymize",
                frozenset({"PERSON", "ADDRESS"}),
                restore_for_tools=True,
                pseudonym_style="natural",
            ),
            Rule("people-output", ("kr_model",), _OUT, "warn", frozenset({"PERSON", "ADDRESS"})),
        ),
    ),
    # High-sensitivity work (HR, finance, health): identifiers and secrets stop the call
    # before anything leaves; contacts are replaced; names and addresses become pseudonyms.
    "kr-strict": RuleSet(
        "kr-strict",
        1,
        (
            Rule("jailbreak", ("jailbreak",), _INPUT, "block"),
            Rule("secrets", ("common",), _IN, "block", frozenset({"SECRET"})),
            Rule("secrets-output", ("common",), _OUT, "redact", frozenset({"SECRET"})),
            Rule("kr-identifiers", ("kr_model",), _IN, "block", _IDS),
            Rule("kr-identifiers-output", ("kr_model",), _OUT, "redact", _IDS),
            Rule(
                "contacts",
                ("common", "kr_model"),
                _SEND_OUT,
                "redact",
                frozenset({"PHONE", "EMAIL"}),
            ),
            Rule(
                "people",
                ("kr_model",),
                _SEND,
                "pseudonymize",
                frozenset({"PERSON", "ADDRESS"}),
                restore_for_tools=True,
            ),
            Rule("people-output", ("kr_model",), _OUT, "warn", frozenset({"PERSON", "ADDRESS"})),
        ),
    ),
    # Language-neutral basics without the Korean packs or the model service: secrets, and
    # email, card and IP values from the common pack.
    "common-basic": RuleSet(
        "common-basic",
        1,
        (
            Rule("jailbreak", ("jailbreak",), _INPUT, "warn"),
            Rule("secrets", ("common",), _IN, "block", frozenset({"SECRET"})),
            Rule("secrets-output", ("common",), _OUT, "redact", frozenset({"SECRET"})),
            Rule("cards", ("common",), _ALL, "redact", frozenset({"CARD"})),
            Rule("contacts", ("common",), _SEND_OUT, "mask", frozenset({"EMAIL", "IP"})),
        ),
    ),
    # Observation mode: every finding recorded, nothing changed.
    "kr-observe": RuleSet(
        "kr-observe",
        3,
        (
            Rule("observe", ("common", "kr_model"), _ALL, "warn"),
            Rule("jailbreak", ("jailbreak",), _INPUT, "warn"),
        ),
    ),
}


def builtin(name: str) -> RuleSet | None:
    return BUILTIN_TEMPLATES.get(name) if name else None
