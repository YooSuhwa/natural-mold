"""Curated overrides + alias resolution + exclusion rules.

These tiny helpers keep the merge step free of policy code: ``aliases``
canonicalize misspelled or alternate model names, ``overrides`` win over
every upstream source for ``provider/model`` rows, and ``excluded`` filters
rows we never want to surface (deprecated, sample stubs, deployment knobs).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_aliases(model_name: str, aliases: dict[str, str]) -> str:
    """Resolve ``model_name`` through the alias table — return the canonical name."""

    if not model_name:
        return model_name
    return aliases.get(model_name, model_name)


def derive_aliases_from_ai_model_list(payload: Any) -> dict[str, str]:
    """Auto-build an alias map from ai-model-list ``models[*].aliases``.

    ai-model-list publishes one canonical name per model (e.g. ``claude-3-5-sonnet``)
    plus a list of ``aliases`` covering provider-prefixed and date-stamped
    variants. We invert that into ``{alias_model_part: canonical}`` so the
    merge step can collapse raw-source rows like ``claude-3-5-sonnet-20241022``
    onto the same canonical row as the curated entry.

    Only the model-name portion of each alias is indexed (provider prefixes
    are stripped) — the merge groups by ``(provider, model)`` separately, so
    same-canonical rows from different providers stay distinct.
    """

    out: dict[str, str] = {}
    if not isinstance(payload, dict):
        return out
    models = payload.get("models")
    if not isinstance(models, dict):
        return out

    for canonical, node in models.items():
        if not isinstance(canonical, str) or not isinstance(node, dict):
            continue
        # The bare canonical maps to itself — harmless, lets later passes
        # treat the full table uniformly without a None check.
        out.setdefault(canonical, canonical)
        for alias in node.get("aliases") or []:
            if not isinstance(alias, str):
                continue
            tail = alias.split("/", 1)[1] if "/" in alias else alias
            # First-write-wins so curated aliases.json (loaded earlier) keeps
            # precedence over ai-model-list's auto-generated map.
            out.setdefault(tail, canonical)
    return out


def is_excluded(model_name: str, excluded: dict[str, Any]) -> bool:
    """True if ``model_name`` matches an exact id or banned prefix."""

    if not isinstance(excluded, dict):
        return False
    exact = excluded.get("exact_model_ids") or []
    prefixes = excluded.get("prefixes") or []
    if model_name in exact:
        return True
    return any(model_name.startswith(prefix) for prefix in prefixes)


def merge_override(
    base: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    """Shallow merge ``override`` onto ``base`` — non-null fields win.

    Empty dicts/lists are treated as values; only ``None`` triggers
    inheritance from the base.
    """

    if not override:
        return base
    out = dict(base)
    for key, value in override.items():
        if value is not None:
            out[key] = value
    return out
