"""빌더 챗 시스템 프롬프트 로더 (assistant_agent._load_system_prompt 패턴)."""

from __future__ import annotations

import functools
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent / "prompt.md"

_FALLBACK_PROMPT = (
    "You are Moldy's Skill Builder. Edit the skill draft files under {workspace}/ "
    "incrementally with the filesystem tools (edit_file for existing files), run "
    "validate_skill after meaningful edits, and never store secrets in package files."
)


@functools.cache
def _load_template() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Skill builder prompt file not found: %s, using fallback", _PROMPT_PATH)
        return _FALLBACK_PROMPT


def load_skill_builder_prompt(workspace_path: str) -> str:
    """``{workspace}`` 플레이스홀더를 세션 가상 마운트 경로로 치환해 반환."""

    virtual = "/" + workspace_path.strip("/") if workspace_path else "/skill-drafts"
    return _load_template().replace("{workspace}", virtual)


__all__ = ["load_skill_builder_prompt"]
