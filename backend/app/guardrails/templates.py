"""Rule sets as data: JSON for company templates and run snapshots (plan §3.4).

A template is referenced as ``builtin:<name>`` (code, ``rules.BUILTIN_TEMPLATES``) or
``company:<uuid>`` (a published ``guardrail_template_versions`` row). Rules are validated
here once, on write; a stored snapshot is trusted and read back as is.
"""

from __future__ import annotations

import uuid
from typing import Any, cast, get_args

from app.guardrails.detectors import PACKS
from app.guardrails.model_pack import MODEL_PACK
from app.guardrails.rules import Action, Rule, RuleSet, Stage

BUILTIN_PREFIX = "builtin:"
COMPANY_PREFIX = "company:"
MAX_RULES = 40
_STAGES = frozenset(get_args(Stage))
_ACTIONS = frozenset(get_args(Action))
_PACK_NAMES = frozenset(PACKS) | {MODEL_PACK}
_STYLES = frozenset({"tag", "natural"})


class InvalidRulesError(ValueError):
    """The rule list cannot be used; the message says which rule and why."""


def rule_to_json(rule: Rule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "packs": list(rule.packs),
        "stages": sorted(rule.stages),
        "action": rule.action,
        "entities": sorted(rule.entities) if rule.entities is not None else None,
        "restore_for_tools": rule.restore_for_tools,
        "pseudonym_style": rule.pseudonym_style,
    }


def rules_to_json(rules: RuleSet) -> dict[str, Any]:
    return {
        "template": rules.template,
        "version": rules.version,
        "rules": [rule_to_json(rule) for rule in rules.rules],
    }


def rule_from_json(data: Any, index: int) -> Rule:
    if not isinstance(data, dict):
        raise InvalidRulesError(f"rule {index}: not an object")
    rule_id = data.get("id")
    if not isinstance(rule_id, str) or not 0 < len(rule_id) <= 64:  # noqa: PLR2004
        raise InvalidRulesError(f"rule {index}: id must be 1-64 characters")
    packs = data.get("packs")
    if not isinstance(packs, list) or not packs or not set(packs) <= _PACK_NAMES:
        raise InvalidRulesError(f"rule {rule_id}: packs must be among {sorted(_PACK_NAMES)}")
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages or not set(stages) <= _STAGES:
        raise InvalidRulesError(f"rule {rule_id}: stages must be among {sorted(_STAGES)}")
    action = data.get("action")
    if action not in _ACTIONS:
        raise InvalidRulesError(f"rule {rule_id}: action must be one of {sorted(_ACTIONS)}")
    entities = data.get("entities")
    if entities is not None and (
        not isinstance(entities, list)
        or not all(isinstance(e, str) and 0 < len(e) <= 40 for e in entities)  # noqa: PLR2004
    ):
        raise InvalidRulesError(f"rule {rule_id}: entities must be a list of names or null")
    style = data.get("pseudonym_style", "tag")
    if style not in _STYLES:
        raise InvalidRulesError(f"rule {rule_id}: pseudonym_style must be tag or natural")
    restore = data.get("restore_for_tools", False)
    if not isinstance(restore, bool):
        raise InvalidRulesError(f"rule {rule_id}: restore_for_tools must be true or false")
    return Rule(
        rule_id,
        tuple(packs),
        frozenset(stages),
        cast(Action, action),
        frozenset(entities) if entities is not None else None,
        restore_for_tools=restore,
        pseudonym_style=style,
    )


def rules_from_json(items: Any) -> tuple[Rule, ...]:
    if not isinstance(items, list) or not 0 < len(items) <= MAX_RULES:
        raise InvalidRulesError(f"rules must be a list of 1-{MAX_RULES} rules")
    rules = tuple(rule_from_json(item, i) for i, item in enumerate(items))
    ids = [rule.id for rule in rules]
    if len(set(ids)) != len(ids):
        raise InvalidRulesError("rule ids must be unique")
    return rules


def snapshot_from_json(data: dict[str, Any]) -> RuleSet:
    """A run's pinned rule set, as ``rules_to_json`` stored it."""
    return RuleSet(str(data["template"]), int(data["version"]), rules_from_json(data["rules"]))


def company_ref(template_id: uuid.UUID) -> str:
    return f"{COMPANY_PREFIX}{template_id}"


def parse_ref(ref: str) -> tuple[str, str] | None:
    """``("builtin", name)`` or ``("company", uuid)``; ``None`` when malformed."""
    if ref.startswith(BUILTIN_PREFIX) and ref[len(BUILTIN_PREFIX) :]:
        return "builtin", ref[len(BUILTIN_PREFIX) :]
    if ref.startswith(COMPANY_PREFIX):
        try:
            return "company", str(uuid.UUID(ref[len(COMPANY_PREFIX) :]))
        except ValueError:
            return None
    return None
