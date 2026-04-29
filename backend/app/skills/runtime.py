"""Skill runtime helpers — translates DB links into deep-agents inputs.

The legacy executor treats package skills as a directory mount under
``/skills/`` (handled by deep-agents' Filesystem backend). This module
provides a uniform shape (``list[dict]``) so callers don't need to import
the ORM directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.models.skill import AgentSkillLink
from app.skills import service as skill_service


def build_skills_for_agent(
    skill_links: Iterable[AgentSkillLink],
) -> list[dict[str, Any]]:
    """Build a list of skill descriptors for an agent's deep-agents config."""

    return [
        skill_service.to_runtime_dict(link.skill)
        for link in skill_links
        if link.skill is not None
    ]


__all__ = ["build_skills_for_agent"]
