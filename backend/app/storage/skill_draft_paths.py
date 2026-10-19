"""Skill Builder workspace identity has one canonical relative storage path."""

from typing import Final
from uuid import UUID

from app.storage.paths import ensure_relative

SKILL_DRAFTS_ROOT: Final = "skill-drafts"


def skill_draft_storage_path(session_id: UUID) -> str:
    return ensure_relative(f"{SKILL_DRAFTS_ROOT}/{session_id}")
