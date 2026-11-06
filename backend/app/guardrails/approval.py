"""Ask the user before a tool runs when its arguments hold a value a rule names (③, P1).

An ``approval`` rule (stage ``tool_args``) becomes an entry of the agent's ``interrupt_on``
with a ``when`` predicate, so the standard human-in-the-loop middleware asks, the screen
shows its usual approval card and a resume follows the usual path. The predicate is called
again on resume and must give the same answer, or a rejection would be skipped; it
therefore detects with the rule packs only (``common``, ``kr``: no service call, same text
→ same answer) on the arguments as the model wrote them. A tool that already asks for
approval every time keeps its own entry.

Where nobody can approve (scheduled runs: no ``interrupt_on``), ``GuardrailMiddleware``
refuses such a call instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from langchain_core.tools import BaseTool

from app.guardrails.actions import label
from app.guardrails.detectors import detect
from app.guardrails.rules import RuleSet

APPROVAL_PACKS: Final = frozenset({"common", "kr"})
# Deep Agents' own tools, which are not in the agent's tool list.
_BUILTIN_TOOLS: Final = ("write_file", "edit_file", "execute")


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for item in value.values() for t in _texts(item)]
    if isinstance(value, (list, tuple)):
        return [t for item in value for t in _texts(item)]
    return []


def approval_entities(rules: RuleSet, args: Any) -> list[str]:
    """Entities in ``args`` whose strongest rule at ``tool_args`` asks for approval."""
    found: list[str] = []
    for text in _texts(args):
        for finding in detect(text, sorted(APPROVAL_PACKS)):
            rule = rules.rule_for(finding.entity, finding.pack, "tool_args")
            if rule is not None and rule.action == "approval":
                found.append(finding.entity)
    return found


def has_approval_rules(rules: RuleSet | None) -> bool:
    return rules is not None and any(rule.action == "approval" for rule in rules.rules)


def _entry(rules: RuleSet) -> dict[str, Any]:
    def when(request: Any) -> bool:
        return bool(approval_entities(rules, request.tool_call.get("args")))

    def description(tool_call: Any, _state: Any, _runtime: Any) -> str:
        names = sorted({label(e) for e in approval_entities(rules, tool_call.get("args"))})
        return (
            f"가드레일 규칙에 따라 승인이 필요합니다: 도구 인자에 {', '.join(names)}이(가) "
            f"있습니다.\n\n도구: {tool_call.get('name')}"
        )

    return {"allowed_decisions": ["approve", "reject"], "when": when, "description": description}


def with_approvals(
    interrupt_on: dict[str, Any], tools: Iterable[BaseTool], rules: RuleSet
) -> dict[str, Any]:
    """``interrupt_on`` plus a conditional approval for every tool that has none yet."""
    result = dict(interrupt_on)
    entry = _entry(rules)
    for name in [*(tool.name for tool in tools), *_BUILTIN_TOOLS]:
        if not result.get(name):
            result[name] = entry
    return result
