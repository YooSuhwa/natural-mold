"""Run-start (stream head) custom events (plan 13.6; F-06 ``excluded_capabilities``).

The v3 protocol stream ships these once after ``running`` with a stable event id
(``<run_id>:<suffix>``) so a reload/reconnect replay dedupes; the legacy SSE stream ships
``moldy.excluded_capabilities`` right after ``message_start`` (persisted, replayed on attach).

``excluded_capabilities_payload`` aggregates ``Exclusion.as_dict`` rows into per
``(type, reason)`` counts. It rebuilds every item from the two allowed fields only, so a
resource id (or any other key) can never reach the wire — a runner may not see the row.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from app.agent_runtime import event_names

_REASONS = frozenset({"broken", "denied"})


def excluded_capabilities_of(cfg: Any) -> list[dict[str, str]] | None:
    """The root run plan's serialized exclusions (no ids), or ``None`` without a plan."""
    plan = getattr(cfg, "access_plan", None)
    return None if plan is None else plan.excluded_capabilities


def excluded_capabilities_payload(
    items: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """``{"items": [{type, reason, count}], "total": n}`` or ``None`` when nothing was excluded."""
    counts: Counter[tuple[str, str]] = Counter()
    for item in items or ():
        kind = item.get("type")
        reason = item.get("reason")
        if isinstance(kind, str) and kind and reason in _REASONS:
            counts[(kind, str(reason))] += 1
    if not counts:
        return None
    rows = [
        {"type": kind, "reason": reason, "count": count}
        for (kind, reason), count in sorted(counts.items())
    ]
    return {"items": rows, "total": sum(counts.values())}


def head_event_specs(
    *,
    subagent_display_names: dict[str, str] | None,
    recalled_memories: list[dict[str, Any]] | None,
    skill_draft_brief: dict[str, Any] | None,
    excluded_capabilities: list[dict[str, str]] | None,
) -> list[tuple[str, dict[str, Any], str]]:
    """``(name, payload, stable_suffix)`` for each non-empty run-start event, in wire order."""
    specs: list[tuple[str, dict[str, Any], str]] = []
    if subagent_display_names:
        specs.append(
            (event_names.SUBAGENT_NAMES, {"names": dict(subagent_display_names)}, "subagent_names")
        )
    if recalled_memories:
        specs.append(
            (event_names.MEMORY_RECALLED, {"memories": recalled_memories}, "memory_recalled")
        )
    if skill_draft_brief:
        specs.append((event_names.SKILL_DRAFT, skill_draft_brief, "skill_draft"))
    excluded = excluded_capabilities_payload(excluded_capabilities)
    if excluded is not None:
        specs.append((event_names.EXCLUDED_CAPABILITIES, excluded, "excluded_capabilities"))
    return specs


__all__ = [
    "excluded_capabilities_of",
    "excluded_capabilities_payload",
    "head_event_specs",
]
