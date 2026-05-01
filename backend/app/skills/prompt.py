"""Skill prompt builder — translates attached skills into a system prompt block.

Mirrors LambChat's ``loader.build_skills_prompt`` pattern: when an agent has
one or more skills mounted, append a markdown section describing them so the
model knows to ``read_file`` ``/skills/<slug>/SKILL.md`` before invoking
behaviour. This complements the existing "스킬 사용 규칙" block in
``executor._prepare_agent`` — that one teaches *how* to use skills; this one
teaches *which* skills exist.

Accepts either :class:`app.models.skill.Skill` ORM rows or the dict form
emitted by :func:`app.skills.service.to_runtime_dict` so callers don't need
to round-trip the DB just to render a prompt.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def build_skills_prompt(skills: Iterable[Any]) -> str:
    """Render a list of skills as a system prompt block.

    Returns ``""`` when the iterable is empty so callers can unconditionally
    ``+= build_skills_prompt(...)`` without producing a stray header.
    """

    rows = [s for s in skills if s is not None]
    if not rows:
        return ""

    lines: list[str] = [
        "",
        "## Available Skills",
        "The agent has access to the following skills mounted under /skills/:",
    ]
    for skill in rows:
        name = _attr(skill, "name") or _attr(skill, "slug") or "skill"
        slug = _attr(skill, "slug") or name
        description = (_attr(skill, "description") or "").strip() or "(no description)"
        lines.append(f"- **{name}**: {description}")
        lines.append(f"  Read `/skills/{slug}/SKILL.md` for full instructions.")
    lines.append("")
    return "\n".join(lines)


__all__ = ["build_skills_prompt"]
