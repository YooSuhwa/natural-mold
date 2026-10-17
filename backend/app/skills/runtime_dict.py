"""Skill → deep-agents runtime dict (moved out of ``service`` for size; re-exported there)."""

from __future__ import annotations

from typing import Any

from app.models.skill import Skill


def to_runtime_dict(skill: Skill) -> dict[str, Any]:
    """Serialize a skill to the dict deep-agents expects.

    ``description`` is included so :func:`app.skills.prompt.build_skills_prompt`
    can render a meaningful "Available Skills" block without re-fetching the
    ORM rows.
    """

    return {
        "id": str(skill.id),
        "name": skill.name,
        "slug": skill.slug,
        "kind": skill.kind,
        "storage_path": skill.storage_path or "",
        "description": skill.description or "",
    }
